"""
Edição de um clipe já gerado: reajustar o tempo do corte e/ou o estilo da
legenda (inclusive religá-la, trocar fonte/cor/formato, ou desligar de vez)
sem precisar reprocessar o vídeo inteiro (transcrição, seleção de momentos
etc. — só re-renderiza esse clipe específico com os novos parâmetros).
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Clip, Job, Video
from app.routers.download import _clip_to_out
from app.routers.upload import CAPTION_FONT_SIZE_MAX, CAPTION_FONT_SIZE_MIN
from app.schemas import (
    ClipOut,
    validate_caption_color,
    validate_caption_font_family,
    validate_caption_style,
)
from app.services import clip_rendering
from app.services.clip_rendering import remap_pan_track, render_clip, resolve_clip_segments, resolve_job_render_defaults

router = APIRouter(tags=["clips"])


class RecutRequest(BaseModel):
    # Todos os campos são opcionais: None mantém o que o clipe/job já tinha.
    # Isso deixa o mesmo endpoint servir tanto pra "só ajustar o tempo" quanto
    # "só mudar o estilo da legenda sem mexer no corte" quanto os dois juntos.
    start_time: float | None = None
    end_time: float | None = None
    captions_enabled: bool | None = None
    caption_font_size: int | None = None
    caption_color: str | None = None
    caption_style: str | None = None
    caption_font_family: str | None = None


def _validate_caption_overrides(payload: RecutRequest) -> None:
    if payload.caption_font_size is not None and not (
        CAPTION_FONT_SIZE_MIN <= payload.caption_font_size <= CAPTION_FONT_SIZE_MAX
    ):
        raise HTTPException(
            status_code=400,
            detail=f"caption_font_size inválido ({payload.caption_font_size}). Use um valor entre "
            f"{CAPTION_FONT_SIZE_MIN} e {CAPTION_FONT_SIZE_MAX}.",
        )
    for value, validator in (
        (payload.caption_color, validate_caption_color),
        (payload.caption_style, validate_caption_style),
        (payload.caption_font_family, validate_caption_font_family),
    ):
        if value is None:
            continue
        try:
            validator(value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/clips/{clip_id}/recut", response_model=ClipOut)
def recut_clip(clip_id: str, payload: RecutRequest, db: Session = Depends(get_db)):
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clipe não encontrado.")

    start_time = payload.start_time if payload.start_time is not None else clip.start_time
    end_time = payload.end_time if payload.end_time is not None else clip.end_time
    if end_time <= start_time:
        raise HTTPException(status_code=400, detail="end_time deve ser maior que start_time.")
    _validate_caption_overrides(payload)

    video = db.get(Video, clip.video_id)
    if video is None or not video.filepath or not Path(video.filepath).exists():
        raise HTTPException(status_code=404, detail="Vídeo original não encontrado.")

    if video.duration is not None and (start_time < 0 or end_time > video.duration):
        raise HTTPException(
            status_code=400,
            detail=f"Intervalo fora da duração do vídeo original (0 - {video.duration:.2f}s).",
        )

    if not clip.processed_path:
        raise HTTPException(status_code=409, detail="Clipe ainda não possui um arquivo processado para sobrescrever.")

    job = db.get(Job, clip.job_id)
    defaults = resolve_job_render_defaults(job)

    # Cada campo de estilo: usa o valor mandado no request, senão cai pro que
    # o job já tinha configurado (não pro default fixo do sistema) — assim
    # editar só o tempo de um clipe não reseta acidentalmente o estilo dele.
    captions_enabled = payload.captions_enabled if payload.captions_enabled is not None else defaults.captions_enabled
    caption_font_size = payload.caption_font_size if payload.caption_font_size is not None else defaults.caption_font_size
    caption_color = payload.caption_color if payload.caption_color is not None else defaults.caption_color
    caption_style = payload.caption_style if payload.caption_style is not None else defaults.caption_style
    caption_font_family = (
        payload.caption_font_family if payload.caption_font_family is not None else defaults.caption_font_family
    )

    # Um pan MANUAL (ver Clip.manual_pan_keyframes), um override MANUAL de
    # legenda (ver Clip.caption_segments_override), cortes INTERNOS (ver
    # Clip.cut_segments/app/routers/clip_cuts.py) e janelas mutadas pelo
    # filtro de palavrões (ver Clip.mute_ranges) são todos definidos em
    # segundos relativos ao início do corte ATUAL — se o intervalo muda aqui,
    # os quatro apontariam pro momento errado do vídeo/corte. Mais seguro
    # descartar todos e voltar pro automático (detecção de rosto/transcrição
    # do Whisper/clipe sem cortes internos) do que aplicar algo deslocado
    # silenciosamente (o usuário pode reconfigurar os quatro depois do novo
    # recorte via PUT /clips/{id}/pan, /captions, /cuts, e reaplicando o
    # filtro de palavrões).
    #
    # Limitação aceita (sem lock nenhum): se ESSE recut e um PUT /cuts (ou
    # /pan, /captions) do MESMO clipe rodarem ao mesmo tempo, o `clip` em
    # memória aqui pode ficar desatualizado no meio do processamento — a
    # troca atômica final de quem terminar por último "vence" no arquivo,
    # mas o commit no banco de cada request só mexe nos campos que ELE
    # pediu, podendo deixar banco e arquivo descrevendo edições diferentes.
    # Ferramenta de um usuário só: risco baixo o bastante pra não valer um
    # lock por clipe agora.
    time_changed = start_time != clip.start_time or end_time != clip.end_time
    manual_pan_track = None if time_changed else clip.get_manual_pan_keyframes()
    cut_segments = None if time_changed else clip.get_cut_segments()
    # Mesmo tratamento de cut_segments acima (ver Clip.mute_ranges): as
    # janelas mutadas pelo filtro de palavrões (modo "censor") foram
    # calculadas em cima do intervalo ANTIGO — mudar o intervalo invalida
    # elas (o usuário pode reconfigurar rodando o filtro de novo, se quiser).
    mute_ranges = None if time_changed else clip.get_mute_ranges()

    # IMPORTANTE: diferente de manual_pan_track/cut_segments/mute_ranges
    # acima (passados como parâmetro explícito pra resolve_clip_segments/
    # render_clip, então o gate local acima já basta), resolve_clip_segments
    # lê clip.get_caption_segments_override() DIRETO do clip, sem receber
    # gate nenhum — ver app/services/clip_rendering.py. Pra ela não
    # reaproveitar um override órfão (calculado pro intervalo ANTIGO) quando
    # o intervalo muda, limpa ele aqui ANTES de chamar resolve_clip_segments.
    # É a MESMA limpeza que já ia acontecer lá embaixo (bloco "if
    # time_changed"), só adiantada — nada é persistido no banco antes da hora
    # (db.commit() continua só no final), e clip.start_time/clip.end_time em
    # si só são atualizados DEPOIS do render dar certo, como já era.
    if time_changed:
        clip.set_caption_segments_override(None)

    srt_segments, clip_text = resolve_clip_segments(clip, cut_segments, start_time=start_time, end_time=end_time)
    render_pan_track = remap_pan_track(manual_pan_track, cut_segments)

    render_clip(
        clip,
        video,
        job,
        start_time=start_time,
        end_time=end_time,
        captions_enabled=captions_enabled,
        caption_font_size=caption_font_size,
        caption_color=caption_color,
        caption_style=caption_style,
        caption_font_family=caption_font_family,
        srt_segments=srt_segments,
        manual_pan_track=render_pan_track,
        cut_segments=cut_segments,
        mute_ranges=mute_ranges,
    )

    clip.transcript_text = clip_text
    clip.hashtags, clip.suggested_titles = clip_rendering.compute_clip_metadata(clip_text)
    clip.start_time = start_time
    clip.end_time = end_time
    if time_changed:
        clip.set_manual_pan_keyframes(None)
        clip.set_cut_segments(None)
        clip.set_mute_ranges(None)
        # clip.set_caption_segments_override(None) já foi feito mais acima —
        # precisava rodar ANTES de resolve_clip_segments (ver comentário lá).
    db.commit()
    db.refresh(clip)

    return _clip_to_out(clip)
