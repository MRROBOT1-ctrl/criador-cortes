"""
Remoção de trechos do MEIO de um clipe (um silêncio, uma fala descartada, um
"deixa eu ver aqui" constrangedor) — diferente do recut (POST
/clips/{id}/recut), que só move as BORDAS do clipe, aqui o usuário marca
quais sub-trechos [início, fim] (segundos relativos ao clipe ATUAL) devem
ser MANTIDOS; o restante vira um "buraco" removido e os pedaços que sobram
são concatenados sem ele (ver app/services/video_cutter.py,
`cut_and_render_vertical`/`keep_segments`/`remap_time_through_cuts`).
"""
import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Clip, Job, Video
from app.routers.download import _clip_to_out
from app.schemas import ClipOut
from app.services import hashtags
from app.services.clip_rendering import remap_clip_segments, remap_pan_track, resolve_job_render_defaults, safe_rerender
from app.services.transcription import Segment, load_transcript_segments, segments_overlapping, write_srt

router = APIRouter(tags=["clip_cuts"])

# Duração mínima de um segmento MANTIDO — evita cortes internos tão curtos
# que viram ruído no encode (um "segmento" de poucos frames some no meio do
# preset "veryfast"/keyframes e só complica o filtro concat sem ganho real).
MIN_KEEP_SEGMENT_DURATION = 0.3


class CutSegmentsIn(BaseModel):
    # Cada item é [início, fim] em segundos, relativos ao início do clipe
    # ATUAL (0 = clip.start_time) — pares, não objetos, pra bater 1:1 com o
    # que fica salvo em Clip.cut_segments. Lista vazia (ou um único par
    # cobrindo o clipe inteiro) == remove o override e volta ao clipe normal,
    # sem cortes internos.
    keep_segments: list[tuple[float, float]] = Field(default_factory=list)


class CutSegmentsOut(BaseModel):
    keep_segments: list[tuple[float, float]]
    is_active: bool  # False = sem cortes internos (removidos ou nunca configurados)


def _normalize(
    keep_segments: list[tuple[float, float]], clip_duration: float
) -> list[tuple[float, float]] | None:
    """None = "sem override": lista vazia OU um único segmento cobrindo o
    clipe inteiro (com uma tolerância pequena pra erro de arredondamento
    vindo do frontend)."""
    if not keep_segments:
        return None
    if len(keep_segments) == 1:
        s, e = keep_segments[0]
        if s <= 0.01 and e >= clip_duration - 0.01:
            return None
    return keep_segments


def _validate_keep_segments(keep_segments: list[tuple[float, float]], clip_duration: float) -> None:
    prev_end = 0.0
    for i, (s, e) in enumerate(keep_segments):
        if e <= s:
            raise HTTPException(
                status_code=400, detail=f"Segmento {i} inválido: fim ({e}) deve ser maior que início ({s})."
            )
        if e - s < MIN_KEEP_SEGMENT_DURATION:
            raise HTTPException(
                status_code=400,
                detail=f"Segmento {i} muito curto ({e - s:.2f}s) — mínimo de {MIN_KEEP_SEGMENT_DURATION}s.",
            )
        if s < -0.01 or e > clip_duration + 0.01:
            raise HTTPException(
                status_code=400,
                detail=f"Segmento {i} fora dos limites do clipe (0 - {clip_duration:.2f}s).",
            )
        if s < prev_end - 0.01:
            raise HTTPException(
                status_code=400, detail=f"Segmento {i} sobrepõe o anterior ou está fora de ordem."
            )
        prev_end = e


@router.get("/clips/{clip_id}/cuts", response_model=CutSegmentsOut)
def get_cut_segments(clip_id: str, db: Session = Depends(get_db)):
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clipe não encontrado.")
    saved = clip.get_cut_segments()
    return CutSegmentsOut(keep_segments=saved or [], is_active=saved is not None)


@router.put("/clips/{clip_id}/cuts", response_model=ClipOut)
def set_cut_segments(clip_id: str, payload: CutSegmentsIn, db: Session = Depends(get_db)):
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clipe não encontrado.")

    video = db.get(Video, clip.video_id)
    if video is None or not video.filepath or not Path(video.filepath).exists():
        raise HTTPException(status_code=404, detail="Vídeo original não encontrado.")
    if not clip.processed_path:
        raise HTTPException(
            status_code=409, detail="Clipe ainda não possui um arquivo processado para sobrescrever."
        )

    clip_duration = clip.end_time - clip.start_time
    keep_segments = sorted((float(s), float(e)) for s, e in payload.keep_segments)
    _validate_keep_segments(keep_segments, clip_duration)
    normalized = _normalize(keep_segments, clip_duration)

    job = db.get(Job, clip.job_id)
    defaults = resolve_job_render_defaults(job)

    # Legenda: se o clipe tem um texto editado manualmente (ver
    # app/routers/captions.py), reaproveita ELE em vez de recalcular a
    # transcrição pura do Whisper — senão configurar cortes internos
    # descartaria silenciosamente uma correção de legenda já feita. Nos dois
    # casos, `remap_clip_segments` cuida de descontar clip.start_time E
    # remapear pelos novos buracos removidos numa passada só (o override,
    # armazenado em tempo relativo ao clipe/pré-corte, precisa somar
    # `clip.start_time` de volta só pra reaproveitar essa função, que espera
    # tempos ABSOLUTOS no vídeo original).
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
    srt_segments, clip_text = remap_clip_segments(clip_segments, clip.start_time, normalized)

    # Token aleatório no nome: duas edições concorrentes desse mesmo clipe
    # (ex: PUT /cuts e PUT /captions ao mesmo tempo) não podem disputar o
    # MESMO arquivo .srt enquanto cada ffmpeg ainda está lendo o seu.
    tmp_srt_path = settings.data_dir / clip.job_id / f"clip_{clip.index}_cuts_{uuid.uuid4().hex[:8]}.srt"
    srt_path = None
    if defaults.captions_enabled and srt_segments:
        write_srt(srt_segments, tmp_srt_path, offset=0.0)
        srt_path = tmp_srt_path

    # Pan manual: remapeado só PRA ESSA renderização — o que fica salvo em
    # Clip.manual_pan_keyframes continua nas coordenadas originais (ver
    # docstring de remap_pan_track), então cortes internos podem mudar de
    # novo depois sem acumular erro de remapeamento em cima de remapeamento.
    original_pan = clip.get_manual_pan_keyframes()
    render_pan_track = remap_pan_track(original_pan, normalized)

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
        streamer_region=defaults.streamer_region,
        manual_pan_track=render_pan_track,
        keep_segments=normalized,
        relaxing_video_path=defaults.relaxing_video_path,
    )

    clip.set_cut_segments(normalized)
    clip.transcript_text = clip_text
    clip.hashtags = json.dumps(hashtags.generate_hashtags(clip_text), ensure_ascii=False)
    db.commit()
    db.refresh(clip)

    return _clip_to_out(clip)
