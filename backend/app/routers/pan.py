"""
Zoom/crop manual por trecho: deixa o usuário definir "pontos de foco"
(tempo -> posição horizontal) pra sobrescrever o rastreamento automático de
rosto (app/services/face_detection.py::track_horizontal_pan) num clipe
específico — útil quando a detecção automática erra, ou quando o usuário só
quer controlar o enquadramento na mão.

Só se aplica a vídeo SOLO (sem streamer_region/layout de tela dividida — ver
Job.streamer_region): não há suporte ainda pra combinar pan manual com o
layout de streamer+gameplay.
"""
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Clip, Job, Video
from app.routers.download import _clip_to_out
from app.schemas import ClipOut
from app.services.clip_rendering import (
    remap_clip_segments,
    remap_pan_track,
    resolve_job_render_defaults,
    safe_rerender,
)
from app.services.transcription import Segment, load_transcript_segments, segments_overlapping, write_srt
from app.config import settings

router = APIRouter(tags=["pan"])


class PanKeyframe(BaseModel):
    t: float = Field(description="Segundos desde o INÍCIO DO CLIPE (0 = start_time do clipe).")
    cx: float = Field(description="Posição horizontal normalizada (0..1) da largura do vídeo ORIGINAL.")


class PanKeyframesIn(BaseModel):
    # Lista vazia == remove o override e volta pra detecção automática.
    keyframes: list[PanKeyframe] = Field(default_factory=list)


class PanKeyframesOut(BaseModel):
    keyframes: list[PanKeyframe]
    is_manual: bool  # False = está usando detecção automática (sem override salvo)


def _validate_keyframes(keyframes: list[PanKeyframe], clip_duration: float) -> None:
    if not keyframes:
        return
    for kf in keyframes:
        if not (0.0 <= kf.cx <= 1.0):
            raise HTTPException(status_code=400, detail=f"cx inválido ({kf.cx}) — precisa estar entre 0 e 1.")
        if not (0.0 <= kf.t <= clip_duration + 0.01):
            raise HTTPException(
                status_code=400,
                detail=f"t inválido ({kf.t}) — precisa estar entre 0 e a duração do clipe ({clip_duration:.2f}s).",
            )
    times = [kf.t for kf in keyframes]
    # Estrito (< não <=): dois keyframes com o MESMO t fariam
    # _build_pan_crop_x_expr dividir por (t1 - t0) == 0 na expressão do
    # ffmpeg (vira inf/nan, crop quebrado sem erro nenhum) — rejeita aqui em
    # vez de deixar chegar lá.
    if any(t1 <= t0 for t0, t1 in zip(times, times[1:])):
        raise HTTPException(
            status_code=400,
            detail="Os keyframes precisam estar em ordem ESTRITAMENTE crescente de tempo (sem tempos repetidos).",
        )


@router.get("/clips/{clip_id}/pan", response_model=PanKeyframesOut)
def get_pan_keyframes(clip_id: str, db: Session = Depends(get_db)):
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clipe não encontrado.")
    saved = clip.get_manual_pan_keyframes()
    keyframes = [PanKeyframe(t=t, cx=cx) for t, cx in saved] if saved else []
    return PanKeyframesOut(keyframes=keyframes, is_manual=saved is not None)


@router.put("/clips/{clip_id}/pan", response_model=ClipOut)
def set_pan_keyframes(clip_id: str, payload: PanKeyframesIn, db: Session = Depends(get_db)):
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clipe não encontrado.")

    video = db.get(Video, clip.video_id)
    if video is None or not video.filepath or not Path(video.filepath).exists():
        raise HTTPException(status_code=404, detail="Vídeo original não encontrado.")
    if not clip.processed_path:
        raise HTTPException(status_code=409, detail="Clipe ainda não possui um arquivo processado para sobrescrever.")

    job = db.get(Job, clip.job_id)
    defaults = resolve_job_render_defaults(job)
    if defaults.streamer_region is not None:
        raise HTTPException(
            status_code=409,
            detail="Este job usa layout de tela dividida (streamer detectado) — pan manual ainda não é "
            "suportado nesse caso.",
        )

    clip_duration = clip.end_time - clip.start_time
    _validate_keyframes(payload.keyframes, clip_duration)

    keyframes = [(kf.t, kf.cx) for kf in payload.keyframes] or None
    clip.set_manual_pan_keyframes(keyframes)

    # A legenda/hashtags não mudam aqui (só o enquadramento) — reaproveita o
    # texto já transcrito pro intervalo atual do clipe pra religar a mesma
    # legenda de sempre, sem precisar de um recut completo. Se existir uma
    # legenda editada manualmente (ver app/routers/captions.py), usa ELA em
    # vez da transcrição crua do Whisper — senão ajustar só o enquadramento
    # descartaria silenciosamente uma correção de texto já feita (mesmo
    # cuidado que já existe em clips.py::recut_clip e clip_cuts.py). Cortes
    # internos (ver app/routers/clip_cuts.py) passam pelo mesmo remapeamento
    # de tempo de sempre — `remap_clip_segments` cuida disso nos dois casos.
    cut_segments = clip.get_cut_segments()
    caption_override = clip.get_caption_segments_override()
    if caption_override is not None:
        clip_segments = [
            Segment(start=s["start"] + clip.start_time, end=s["end"] + clip.start_time, text=s["text"])
            for s in caption_override
        ]
    else:
        transcript_segments = load_transcript_segments(settings.data_dir / clip.job_id)
        clip_segments = (
            segments_overlapping(transcript_segments, clip.start_time, clip.end_time) if transcript_segments else []
        )
    srt_segments, _ = remap_clip_segments(clip_segments, clip.start_time, cut_segments)
    tmp_srt_path = settings.data_dir / clip.job_id / f"clip_{clip.index}_pan_{uuid.uuid4().hex[:8]}.srt"
    srt_path = None
    if defaults.captions_enabled and srt_segments:
        write_srt(srt_segments, tmp_srt_path, offset=0.0)
        srt_path = tmp_srt_path

    # Os keyframes recém-mandados pelo usuário estão nas coordenadas do
    # clipe ATUAL (pré-corte) — precisam do mesmo remapeamento pra bater com
    # a linha do tempo pós-concat antes de renderizar (o que fica salvo no
    # banco continua pré-corte, ver docstring de remap_pan_track).
    render_pan_track = remap_pan_track(keyframes, cut_segments)

    safe_rerender(
        Path(video.filepath),
        clip.start_time,
        clip.end_time,
        Path(clip.processed_path),
        srt_path=srt_path,
        cleanup_srt=True,
        watermark_path=defaults.watermark_path,
        watermark_position=defaults.watermark_position,
        captions_enabled=defaults.captions_enabled,
        caption_font_size=defaults.caption_font_size,
        caption_color=defaults.caption_color,
        caption_style=defaults.caption_style,
        caption_font_family=defaults.caption_font_family,
        streamer_region=None,
        manual_pan_track=render_pan_track,
        keep_segments=cut_segments,
        relaxing_video_path=defaults.relaxing_video_path,
    )

    db.commit()
    db.refresh(clip)
    return _clip_to_out(clip)
