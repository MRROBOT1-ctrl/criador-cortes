"""
Helpers compartilhados por qualquer endpoint que precise RE-renderizar um
clipe já existente (recut de tempo, edição de legenda, pan manual, etc.) —
extraído de app/routers/clips.py pra evitar duplicar a mesma lógica de
"resolve os defaults do job" e "renderiza com segurança" em cada endpoint novo.
"""
from dataclasses import dataclass
from pathlib import Path

from app.models import Job
from app.routers.watermark import find_watermark_file
from app.services import video_cutter
from app.services.transcription import Segment


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


def resolve_job_render_defaults(job: Job | None) -> JobRenderDefaults:
    watermark_path = find_watermark_file() if (job is not None and job.watermark_enabled) else None
    return JobRenderDefaults(
        watermark_path=watermark_path,
        watermark_position=(job.watermark_position if job else None) or "bottom-right",
        captions_enabled=job.captions_enabled if (job and job.captions_enabled is not None) else True,
        caption_font_size=(job.caption_font_size if job else None) or 8,
        caption_color=(job.caption_color if job else None) or "#FFFFFF",
        caption_style=(job.caption_style if job else None) or "bold_outline",
        caption_font_family=(job.caption_font_family if job else None) or "DejaVu Sans",
        streamer_region=job.get_streamer_region() if job is not None else None,
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
        result = video_cutter.remap_range_through_cuts(seg.start - clip_start, seg.end - clip_start, keep_segments)
        if result is None:
            continue  # linha caiu inteira (ou quase) num trecho removido — ver remap_range_through_cuts
        new_start, new_end = result
        remapped.append(Segment(start=new_start, end=new_end, text=seg.text))
        texts.append(seg.text)
    return remapped, " ".join(texts).strip()


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
    """Renderiza num arquivo temporário e só substitui `output_path` se o
    ffmpeg terminar com sucesso — uma falha no meio do encode (disco cheio,
    processo morto) não deve sobrescrever o clipe anterior (que funcionava)
    com um arquivo truncado/corrompido. `cleanup_srt=True` apaga o .srt
    temporário depois (usado quando o chamador gerou um só pra essa
    renderização, não um arquivo persistente)."""
    tmp_path = output_path.with_suffix(".recut_tmp.mp4")
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
