"""
Edição de um clipe já gerado: reajustar o tempo do corte e/ou o estilo da
legenda (inclusive religá-la, trocar fonte/cor/formato, ou desligar de vez)
sem precisar reprocessar o vídeo inteiro (transcrição, seleção de momentos
etc. — só re-renderiza esse clipe específico com os novos parâmetros).
"""
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
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
from app.services import hashtags
from app.services.clip_rendering import resolve_job_render_defaults, safe_rerender
from app.services.transcription import load_transcript_segments, segments_overlapping, write_srt

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

    output_path = Path(clip.processed_path)
    job = db.get(Job, clip.job_id)
    defaults = resolve_job_render_defaults(job)

    # Cada campo de estilo: usa o valor mandado no request, senão cai pro que
    # o job já tinha configurado (não pro default fixo do sistema) — assim
    # editar só o tempo de um clipe não reseta acidentalmente o estilo dele.
    captions_enabled = payload.captions_enabled if payload.captions_enabled is not None else defaults.captions_enabled
    caption_font_size = payload.caption_font_size or defaults.caption_font_size
    caption_color = payload.caption_color or defaults.caption_color
    caption_style = payload.caption_style or defaults.caption_style
    caption_font_family = payload.caption_font_family or defaults.caption_font_family

    # Recalcula os segmentos de transcrição que caem no NOVO intervalo — usado
    # tanto pra montar a legenda (se habilitada) quanto pra atualizar
    # transcript_text/hashtags do clipe, que senão ficariam descrevendo o
    # trecho antigo (já descartado).
    transcript_segments = load_transcript_segments(settings.data_dir / clip.job_id)
    clip_segments = segments_overlapping(transcript_segments, start_time, end_time) if transcript_segments else []
    clip_text = " ".join(seg.text for seg in clip_segments).strip()

    # .srt recalculado com offset pro novo início do corte — só existe (e só
    # é passado adiante) se a legenda estiver habilitada pra esse render;
    # antes o recut sempre rodava sem legenda nenhuma por simplicidade, mas
    # com os segmentos já recalculados aqui não custa nada religar direito.
    tmp_srt_path = settings.data_dir / clip.job_id / f"clip_{clip.index}_recut.srt"
    srt_path = None
    if captions_enabled and clip_segments:
        write_srt(clip_segments, tmp_srt_path, offset=start_time)
        srt_path = tmp_srt_path

    # Um pan MANUAL (ver Clip.manual_pan_keyframes) é definido em segundos
    # relativos ao início do corte ATUAL — se o intervalo muda aqui, os
    # keyframes antigos apontariam pro momento errado do vídeo. Mais seguro
    # descartar e voltar pra detecção automática do que aplicar um pan
    # deslocado silenciosamente (edição de legenda via PUT /clips/{id}/pan
    # continua disponível pro usuário refazer depois do novo recorte).
    time_changed = start_time != clip.start_time or end_time != clip.end_time
    manual_pan_track = None if time_changed else clip.get_manual_pan_keyframes()

    safe_rerender(
        Path(video.filepath),
        start_time,
        end_time,
        output_path,
        srt_path=srt_path,
        cleanup_srt=True,
        watermark_path=defaults.watermark_path,
        watermark_position=defaults.watermark_position,
        captions_enabled=captions_enabled,
        caption_font_size=caption_font_size,
        caption_color=caption_color,
        caption_style=caption_style,
        caption_font_family=caption_font_family,
        streamer_region=defaults.streamer_region,
        manual_pan_track=manual_pan_track,
    )

    clip.transcript_text = clip_text
    clip.hashtags = json.dumps(hashtags.generate_hashtags(clip_text), ensure_ascii=False)
    clip.start_time = start_time
    clip.end_time = end_time
    if time_changed:
        clip.set_manual_pan_keyframes(None)
    db.commit()
    db.refresh(clip)

    return _clip_to_out(clip)
