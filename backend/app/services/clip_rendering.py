"""
Helpers compartilhados por qualquer endpoint que precise RE-renderizar um
clipe já existente (recut de tempo, edição de legenda, pan manual, etc.) —
extraído de app/routers/clips.py pra evitar duplicar a mesma lógica de
"resolve os defaults do job" e "renderiza com segurança" em cada endpoint novo.
"""
import json
import logging
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Clip, Job, Video
from app.routers.watermark import find_watermark_file
from app.services import hashtags, titles, video_cutter
from app.services.caption_style import resolve_caption_style
from app.services.transcription import Segment, load_transcript_segments, segments_overlapping, write_srt

logger = logging.getLogger(__name__)

# Sentinela usado só por render_clip() (ver `mute_ranges` lá embaixo) — existe
# porque `None` já é um valor válido e com significado próprio pra alguns
# parâmetros ("descarta de propósito", não "não mexeu nisso"). Usar `None`
# como default normal não deixaria diferenciar "o chamador não passou nada"
# de "o chamador passou None de propósito" — só um objeto que NINGUÉM passaria
# por acidente resolve isso.
_UNSET = object()


# ---------------------------------------------------------------------------
# Lock por clipe (contrato §0.5)
#
# Toda renderização de um clipe existente (PUT /edit, recut, cuts, pan,
# captions) gera um arquivo NOVO e depois troca o processed_path no banco.
# Duas renderizações do MESMO clipe ao mesmo tempo calculariam o mesmo nome
# `_r{n+1}.mp4` e uma gravaria por cima do resultado da outra — por isso só
# uma por clipe de cada vez. `acquire` NÃO bloqueante: quem chega depois
# recebe 409 na hora em vez de ficar esperando um ffmpeg de 10-40s.
# Em memória porque a API roda num processo só; se um dia houver vários, o
# compare-and-swap do /edit (WHERE revision = ?) continua protegendo o banco.
# ---------------------------------------------------------------------------

RENDER_IN_PROGRESS_MESSAGE = "Outra edição deste corte está renderizando. Tente de novo em alguns segundos."

_busy_clips: set[str] = set()
_busy_guard = threading.Lock()


class RenderInProgressError(HTTPException):
    """409 render_in_progress já no envelope novo — subclasse de HTTPException
    pra que os endpoints antigos (que continuam com erros {"detail": "texto"})
    não precisem traduzir nada: basta deixar a exceção subir."""

    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            detail={"code": "render_in_progress", "message": RENDER_IN_PROGRESS_MESSAGE},
        )


@contextmanager
def clip_render_lock(clip_id: str) -> Iterator[None]:
    """Segura o lock do clipe durante o bloco; levanta RenderInProgressError
    se outra renderização do mesmo clipe já estiver rodando. Liberado no
    `finally`, inclusive quando o render falha."""
    with _busy_guard:
        if clip_id in _busy_clips:
            raise RenderInProgressError()
        _busy_clips.add(clip_id)
    try:
        yield
    finally:
        with _busy_guard:
            _busy_clips.discard(clip_id)


def next_render_path(clip: Clip) -> Path:
    """Arquivo da PRÓXIMA versão do clipe: `clip_{index}_r{revision+1}.mp4`,
    na mesma pasta do atual (contrato §0.5). O arquivo atual nunca é
    sobrescrito: só depois do commit o processed_path passa a apontar pro
    novo, então uma falha no meio deixa o clipe anterior intacto e válido."""
    return Path(clip.processed_path).parent / f"clip_{clip.index}_r{(clip.revision or 0) + 1}.mp4"


def discard_old_render(old_path: str | None, new_path: Path) -> None:
    """Apaga o arquivo anterior depois do commit (best-effort: se falhar, só
    sobra um arquivo órfão, o banco já aponta pro novo)."""
    if not old_path or Path(old_path) == new_path:
        return
    try:
        Path(old_path).unlink(missing_ok=True)
    except OSError:
        logger.warning("Não consegui apagar o render anterior %s — fica órfão.", old_path, exc_info=True)


def commit_render(db: Session, clip: Clip, new_path: Path) -> None:
    """Fecha uma renderização dos endpoints ANTIGOS (pan, captions, cuts):
    aponta processed_path pro arquivo novo, faz `revision += 1` (sem isso
    eles passariam por baixo do compare-and-swap do /edit) e commita junto
    com o que o router já alterou no `clip`. Se o commit falhar: rollback,
    apaga o arquivo novo e relança — o banco continua no arquivo antigo, que
    ainda existe. Precisa rodar DENTRO do clip_render_lock."""
    old_path = clip.processed_path
    clip.processed_path = str(new_path)
    clip.revision = (clip.revision or 0) + 1
    try:
        db.commit()
    except Exception:
        db.rollback()
        new_path.unlink(missing_ok=True)
        raise
    discard_old_render(old_path, new_path)


@dataclass
class JobRenderDefaults:
    """Parâmetros de renderização herdados do Job — usados como fallback
    quando um endpoint de edição não está mexendo naquele aspecto específico
    (ex: editar só a legenda não deve resetar marca d'água/streamer_region)."""

    watermark_path: Path | None
    watermark_position: str
    captions_enabled: bool
    caption_font_size: int
    caption_color: str
    caption_style: str
    caption_font_family: str
    streamer_region: tuple[int, int, int, int] | None
    relaxing_video_path: Path | None


def resolve_job_render_defaults(job: Job | None) -> JobRenderDefaults:
    watermark_path = find_watermark_file() if (job is not None and job.watermark_enabled) else None

    # Mesma robustez de find_watermark_file: o vídeo relaxante escolhido pro
    # job pode ter sido apagado da biblioteca depois (ver
    # app/routers/relaxing_videos.py) — trata como "sem vídeo relaxante" em
    # vez de quebrar o render.
    relaxing_video_path = None
    if job is not None and job.relaxing_video is not None and job.relaxing_video.filepath:
        candidate = Path(job.relaxing_video.filepath)
        if candidate.exists():
            relaxing_video_path = candidate

    return JobRenderDefaults(
        watermark_path=watermark_path,
        watermark_position=(job.watermark_position if job else None) or "bottom-right",
        captions_enabled=job.captions_enabled if (job and job.captions_enabled is not None) else True,
        caption_font_size=(job.caption_font_size if job else None) or 8,
        caption_color=(job.caption_color if job else None) or "#FFFFFF",
        caption_style=(job.caption_style if job else None) or "bold_outline",
        caption_font_family=(job.caption_font_family if job else None) or "DejaVu Sans",
        streamer_region=job.get_streamer_region() if job is not None else None,
        relaxing_video_path=relaxing_video_path,
    )


def remap_clip_segments(
    clip_segments: list[Segment],
    clip_start: float,
    keep_segments: list[tuple[float, float]] | None,
) -> tuple[list[Segment], str]:
    """Converte segmentos de transcrição (tempos ABSOLUTOS no vídeo original,
    do jeito que `load_transcript_segments`/`segments_overlapping` devolvem)
    pra tempos relativos ao INÍCIO DO RESULTADO FINAL desse clipe — já
    descontando `clip_start` e, se o clipe tiver cortes internos
    (`keep_segments`, ver app/routers/clip_cuts.py), remapeando pelo sistema
    pós-corte via `video_cutter.remap_range_through_cuts`. O chamador sempre
    escreve o .srt resultante com `offset=0.0`, com ou sem cortes internos —
    evita ter dois jeitos de calcular o offset espalhados pelos routers.

    Usada por recut_clip, pan.py e clip_cuts.py — os três precisam desse
    mesmo cálculo (a legenda existente reflete o texto do trecho, mas os
    cortes internos podem ter descartado/truncado partes dele).

    Retorna (segmentos_prontos_pro_srt, texto_sobrevivente) — o texto usado
    pra recalcular transcript_text/hashtags só conta o que realmente restou
    no vídeo final (linhas inteiramente dentro de um buraco removido não
    entram nem no texto).
    """
    if keep_segments is None:
        segments = [
            Segment(start=max(0.0, seg.start - clip_start), end=max(0.0, seg.end - clip_start), text=seg.text)
            for seg in clip_segments
        ]
        text = " ".join(seg.text for seg in clip_segments).strip()
        return segments, text

    remapped: list[Segment] = []
    texts: list[str] = []
    for seg in clip_segments:
        # Clamp igual ao branch sem cortes internos acima — um segmento pode
        # legitimamente começar um pouco ANTES de clip_start (segments_overlapping
        # aceita >=50% de sobreposição, não exige o segmento inteiro dentro do
        # intervalo). Sem isso, `seg.start - clip_start` fica negativo e
        # remap_time_through_cuts trata como "antes do primeiro segmento
        # mantido" — descartando a linha à toa mesmo quando ela sobrevive.
        local_start = max(0.0, seg.start - clip_start)
        local_end = max(0.0, seg.end - clip_start)
        result = video_cutter.remap_range_through_cuts(local_start, local_end, keep_segments)
        if result is None:
            continue  # linha caiu inteira (ou quase) num trecho removido — ver remap_range_through_cuts
        new_start, new_end = result
        remapped.append(Segment(start=new_start, end=new_end, text=seg.text))
        texts.append(seg.text)
    return remapped, " ".join(texts).strip()


def resolve_clip_segments(
    clip: Clip,
    cut_segments: list[tuple[float, float]] | None,
    start_time: float | None = None,
    end_time: float | None = None,
) -> tuple[list[Segment], str]:
    """Resolve os segmentos de legenda EFETIVOS de um clipe — override
    manual (Clip.caption_segments_override) se existir, senão a transcrição
    do Whisper no intervalo [start_time, end_time] — já remapeados por
    `cut_segments` (cortes internos, se houver, ver remap_clip_segments
    acima). Retorna (segmentos_prontos_pro_srt, texto_sobrevivente) — extraído
    de app/routers/pan.py, captions.py e clip_cuts.py, que tinham esse mesmo
    bloco duplicado quase idêntico.

    `start_time`/`end_time` default pra clip.start_time/clip.end_time —
    suficiente pros 3 endpoints que nunca movem o intervalo do clipe (pan,
    captions, clip_cuts: o clipe em si não muda de tempo nesses três, só o
    que é feito com ele). Só clips.py::recut_clip precisa passar os dois
    explicitamente: no branch SEM override manual de legenda, ele recalcula
    a partir de um intervalo NOVO — que só é gravado em clip.start_time/
    clip.end_time DEPOIS do render dar certo (ver recut_clip), então nesse
    momento clip.start_time/clip.end_time ainda são os valores ANTIGOS."""
    effective_start = start_time if start_time is not None else clip.start_time
    effective_end = end_time if end_time is not None else clip.end_time

    caption_override = clip.get_caption_segments_override()
    if caption_override is not None:
        # Override é guardado relativo ao início do clipe — soma
        # effective_start de volta só pra reaproveitar remap_clip_segments,
        # que espera tempos ABSOLUTOS no vídeo original (ela subtrai de novo).
        clip_segments = [
            Segment(start=s["start"] + effective_start, end=s["end"] + effective_start, text=s["text"])
            for s in caption_override
        ]
    else:
        transcript_segments = load_transcript_segments(settings.data_dir / clip.job_id)
        clip_segments = (
            segments_overlapping(transcript_segments, effective_start, effective_end)
            if transcript_segments else []
        )
    return remap_clip_segments(clip_segments, effective_start, cut_segments)


def compute_clip_metadata(text: str) -> tuple[str, str]:
    """Recalcula hashtags + títulos sugeridos a partir do texto do trecho,
    já como strings JSON prontas pra Clip.hashtags/Clip.suggested_titles —
    centraliza isso num lugar só (era duplicado entre pipeline.py, recut_clip,
    set_captions e set_cut_segments)."""
    hashtags_json = json.dumps(hashtags.generate_hashtags(text), ensure_ascii=False)
    titles_json = json.dumps(titles.generate_titles(text), ensure_ascii=False)
    return hashtags_json, titles_json


def remap_mute_ranges(
    mute_ranges: list[tuple[float, float]] | None,
    keep_segments: list[tuple[float, float]] | None,
) -> list[tuple[float, float]] | None:
    """Mesmo espírito de remap_pan_track, mas pra INTERVALOS (não pontos) —
    usado pro filtro de palavrões no modo "censor" (ver Clip.mute_ranges).
    Um intervalo mutado que caia inteiramente dentro de um trecho removido
    pelos cortes internos simplesmente desaparece (nada pra mutar ali, o
    trecho nem existe mais no resultado final)."""
    if mute_ranges is None or keep_segments is None:
        return mute_ranges
    remapped: list[tuple[float, float]] = []
    for s, e in mute_ranges:
        result = video_cutter.remap_range_through_cuts(s, e, keep_segments)
        if result is not None:
            remapped.append(result)
    return remapped or None


def remap_pan_track(
    pan_track: list[tuple[float, float]] | None,
    keep_segments: list[tuple[float, float]] | None,
) -> list[tuple[float, float]] | None:
    """Remapeia um rastro de pan manual (lista de (t, cx), t relativo ao
    início do clipe) pelo sistema pós-corte — descarta os keyframes que caem
    num trecho removido. `pan_track`/`keep_segments` None (sem pan manual
    salvo, ou sem cortes internos) devolve `pan_track` sem alteração.

    IMPORTANTE: isso é só pra uso NA HORA DE RENDERIZAR — o valor persistido
    em `Clip.manual_pan_keyframes` continua sempre nas coordenadas ORIGINAIS
    (pré-corte); remapear e sobrescrever o campo salvo faria o resultado
    degradar a cada edição de cortes (remapeamento em cima de remapeamento).
    """
    if pan_track is None or keep_segments is None:
        return pan_track
    remapped = [
        (new_t, cx)
        for t, cx in pan_track
        if (new_t := video_cutter.remap_time_through_cuts(t, keep_segments)) is not None
    ]
    return remapped or None


def safe_rerender(
    video_path: Path,
    start: float,
    end: float,
    output_path: Path,
    *,
    srt_path: Path | None = None,
    cleanup_srt: bool = False,
    **cutter_kwargs,
) -> None:
    """Renderiza num arquivo temporário e só move pra `output_path` se o
    ffmpeg terminar com sucesso — uma falha no meio do encode (disco cheio,
    processo morto) não deixa um arquivo truncado/corrompido com o nome
    final. `cleanup_srt=True` apaga o .srt temporário depois (usado quando o
    chamador gerou um só pra essa renderização, não um arquivo persistente).

    Desde a Timeline de Revisão v1, `output_path` é sempre um arquivo NOVO
    (`clip_{index}_r{n+1}.mp4`, ver next_render_path) e a concorrência no
    mesmo clipe é barrada antes, pelo clip_render_lock. O token aleatório no
    nome do tmp continua: custa nada e evita colisão se algum chamador futuro
    renderizar fora do lock."""
    tmp_path = output_path.with_suffix(f".{uuid.uuid4().hex[:8]}.recut_tmp.mp4")
    try:
        video_cutter.cut_and_render_vertical(
            video_path, start, end, tmp_path, srt_path=srt_path, **cutter_kwargs,
        )
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    finally:
        if cleanup_srt and srt_path is not None:
            srt_path.unlink(missing_ok=True)
    tmp_path.replace(output_path)  # troca atômica (mesmo filesystem) só após sucesso


def render_clip(
    clip: Clip,
    video: Video,
    job: Job | None,
    *,
    start_time: float | None = None,
    end_time: float | None = None,
    captions_enabled: bool | None = None,
    caption_font_size: int | None = None,
    caption_color: str | None = None,
    caption_style: str | None = None,
    caption_font_family: str | None = None,
    srt_segments: list[Segment] | None = None,
    manual_pan_track: list[tuple[float, float]] | None = None,
    cut_segments: list[tuple[float, float]] | None = None,
    mute_ranges: list[tuple[float, float]] | None | object = _UNSET,
) -> Path:
    """Centraliza a chamada de safe_rerender aplicando os defaults do JOB
    (via resolve_job_render_defaults) pra tudo que não foi explicitamente
    sobrescrito por parâmetro — cada router só passa o que É diferente do
    default, em vez de repetir os mesmos ~12 kwargs toda vez (eram idênticos
    em captions.py, clip_cuts.py, clips.py e pan.py).

    Renderiza num arquivo NOVO (`next_render_path`, `clip_{index}_r{n+1}.mp4`)
    e devolve o caminho dele — não mexe no processed_path nem no banco. Quem
    chama grava e commita (commit_render nos endpoints antigos, o
    compare-and-swap em app/services/clip_edit.py no /edit e no recut) e só
    então o arquivo antigo é apagado. Precisa rodar DENTRO do
    clip_render_lock, com `clip.revision` atualizado (senão o nome do
    arquivo novo pode coincidir com o de outra renderização).

    Estilo da legenda: cada parâmetro `caption_*`/`captions_enabled` omitido
    cai no estilo EFETIVO do CLIPE (resolve_caption_style: Clip > Job >
    default), não mais só no do Job — sem isso, mexer no zoom depois de
    trocar a cor do clipe voltava silenciosamente à cor do Job (spec §2.1,
    métrica #5).

    `start_time`/`end_time`: default pro intervalo ATUAL do clipe
    (clip.start_time/clip.end_time) — só recut_clip passa um intervalo NOVO
    explicitamente (mesmo motivo de resolve_clip_segments acima).

    `srt_segments`: já resolvidos pelo chamador (normalmente via
    resolve_clip_segments) — essa função só escreve o .srt TEMPORÁRIO (se o
    `captions_enabled` resultante permitir) e deixa o safe_rerender limpar
    ele depois (cleanup_srt=True). Quem chama continua responsável por virar
    esse retorno em transcript_text/hashtags/títulos (ver
    compute_clip_metadata) — render_clip só cuida do arquivo de vídeo.

    `manual_pan_track`/`cut_segments`: sempre explícitos por quem chama —
    cada router já sabe se deve usar o valor salvo no clipe, um valor NOVO
    ainda não salvo, ou um remapeado (ver comentários em cada router). Essa
    função não tenta adivinhar, só repassa pro safe_rerender.

    `mute_ranges`: ÚNICO parâmetro com semântica de 3 estados, por isso o
    sentinel `_UNSET` como default (em vez de `None`):
      - omitido (default) = usa `clip.get_mute_ranges()` — "não mexe nisso,
        preserva o que já tava configurado" (pan.py, captions.py, e
        recut_clip quando o intervalo NÃO muda);
      - lista explícita = usa ela (já remapeada pelo chamador quando
        necessário — ver clip_cuts.py, que remapeia pelos cortes internos
        NOVOS antes de passar aqui);
      - `None` EXPLÍCITO = descarta de propósito (recut_clip quando o
        intervalo MUDA — o mute antigo foi calculado pro trecho antigo, não
        faz mais sentido no novo).
    Não confundir os dois últimos era exatamente o bug que o pan.py tinha
    antes desse refactor: nem passava o parâmetro pro safe_rerender, então
    um clipe com palavra censurada (filtro de palavrões, modo "censor")
    perdia o mute silenciosamente ao ajustar o zoom manual — ver fix
    correspondente em app/routers/pan.py.
    """
    defaults = resolve_job_render_defaults(job)
    clip_style = resolve_caption_style(clip, job)
    render_start = start_time if start_time is not None else clip.start_time
    render_end = end_time if end_time is not None else clip.end_time
    effective_mute_ranges = clip.get_mute_ranges() if mute_ranges is _UNSET else mute_ranges
    resolved_captions_enabled = captions_enabled if captions_enabled is not None else clip_style.enabled
    output_path = next_render_path(clip)

    tmp_srt_path = settings.data_dir / clip.job_id / f"clip_{clip.index}_rerender_{uuid.uuid4().hex[:8]}.srt"
    srt_path = None
    if resolved_captions_enabled and srt_segments:
        write_srt(srt_segments, tmp_srt_path, offset=0.0)
        srt_path = tmp_srt_path

    safe_rerender(
        Path(video.filepath),
        render_start,
        render_end,
        output_path,
        srt_path=srt_path,
        cleanup_srt=True,
        watermark_path=defaults.watermark_path,
        watermark_position=defaults.watermark_position,
        captions_enabled=resolved_captions_enabled,
        caption_font_size=caption_font_size if caption_font_size is not None else clip_style.font_size,
        caption_color=caption_color if caption_color is not None else clip_style.color,
        caption_style=caption_style if caption_style is not None else clip_style.preset,
        caption_font_family=caption_font_family if caption_font_family is not None else clip_style.font_family,
        streamer_region=defaults.streamer_region,
        manual_pan_track=manual_pan_track,
        keep_segments=cut_segments,
        mute_ranges=effective_mute_ranges,
        relaxing_video_path=defaults.relaxing_video_path,
    )
    return output_path
