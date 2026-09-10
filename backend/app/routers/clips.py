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
from app.routers.watermark import find_watermark_file
from app.schemas import (
    ClipOut,
    validate_caption_color,
    validate_caption_font_family,
    validate_caption_style,
)
from app.services import hashtags, video_cutter
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

    # Cada campo de estilo: usa o valor mandado no request, senão cai pro que
    # o job já tinha configurado (não pro default fixo do sistema) — assim
    # editar só o tempo de um clipe não reseta acidentalmente o estilo dele.
    captions_enabled = payload.captions_enabled if payload.captions_enabled is not None else (
        job.captions_enabled if job else True
    )
    caption_font_size = payload.caption_font_size or (job.caption_font_size if job else 8)
    caption_color = payload.caption_color or (job.caption_color if job else "#FFFFFF")
    caption_style = payload.caption_style or (job.caption_style if job else "bold_outline")
    caption_font_family = payload.caption_font_family or (job.caption_font_family if job else "DejaVu Sans")

    # Mantém a marca d'água do job original (se estava ligada) — o usuário
    # não espera que reajustar o corte manualmente remova a logo do clipe.
    watermark_path = find_watermark_file() if (job is not None and job.watermark_enabled) else None

    # Idem pro layout de tela dividida (streamer detectado): reaplica a
    # mesma região persistida no Job (ver Job.set_streamer_region em
    # app/models.py) em vez de reverter silenciosamente pro crop central.
    streamer_region = job.get_streamer_region() if job is not None else None

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

    # Renderiza num arquivo temporário e só substitui o clipe atual se o
    # ffmpeg terminar com sucesso — sem isso, uma falha no meio do encode
    # (disco cheio, processo morto) sobrescrevia o mp4 anterior (que
    # funcionava) com um arquivo truncado/corrompido, e a API ainda retornava
    # erro 500 sem repor o que existia antes.
    tmp_path = output_path.with_suffix(".recut_tmp.mp4")
    try:
        video_cutter.cut_and_render_vertical(
            Path(video.filepath),
            start_time,
            end_time,
            tmp_path,
            srt_path=srt_path,
            watermark_path=watermark_path,
            watermark_position=(job.watermark_position if job else None) or "bottom-right",
            captions_enabled=captions_enabled,
            caption_font_size=caption_font_size,
            caption_color=caption_color,
            caption_style=caption_style,
            caption_font_family=caption_font_family,
            streamer_region=streamer_region,
        )
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    finally:
        if srt_path is not None:
            srt_path.unlink(missing_ok=True)
    tmp_path.replace(output_path)  # troca atômica (mesmo filesystem) só após sucesso

    clip.transcript_text = clip_text
    clip.hashtags = json.dumps(hashtags.generate_hashtags(clip_text), ensure_ascii=False)
    clip.start_time = start_time
    clip.end_time = end_time
    db.commit()
    db.refresh(clip)

    return _clip_to_out(clip)
