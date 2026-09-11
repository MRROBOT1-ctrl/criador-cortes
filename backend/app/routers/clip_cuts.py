"""
Remoção de trechos do MEIO de um clipe (um silêncio, uma fala descartada, um
"deixa eu ver aqui" constrangedor) — diferente do recut (POST
/clips/{id}/recut), que só move as BORDAS do clipe, aqui o usuário marca
quais sub-trechos [início, fim] (segundos relativos ao clipe ATUAL) devem
ser MANTIDOS; o restante vira um "buraco" removido e os pedaços que sobram
são concatenados sem ele (ver app/services/video_cutter.py,
`cut_and_render_vertical`/`keep_segments`/`remap_time_through_cuts`).
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Clip, Job, Video
from app.routers.download import _clip_to_out
from app.schemas import ClipOut
from app.services import clip_rendering
from app.services.clip_rendering import remap_mute_ranges, remap_pan_track, render_clip, resolve_clip_segments

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

    # Legenda: resolve_clip_segments já reaproveita um texto editado
    # manualmente (ver app/routers/captions.py) em vez de recalcular a
    # transcrição pura do Whisper — senão configurar cortes internos
    # descartaria silenciosamente uma correção de legenda já feita. IMPORTANTE
    # aqui: passa `normalized` (os cortes NOVOS recém-validados, ainda não
    # salvos no clipe) — não clip.get_cut_segments() (o valor ANTIGO) —, pra
    # já remapear a legenda pelos buracos que só vão existir DEPOIS desse PUT.
    srt_segments, clip_text = resolve_clip_segments(clip, normalized)

    # Pan manual: remapeado só PRA ESSA renderização — o que fica salvo em
    # Clip.manual_pan_keyframes continua nas coordenadas originais (ver
    # docstring de remap_pan_track), então cortes internos podem mudar de
    # novo depois sem acumular erro de remapeamento em cima de remapeamento.
    original_pan = clip.get_manual_pan_keyframes()
    render_pan_track = remap_pan_track(original_pan, normalized)

    # Janelas mutadas pelo filtro de palavrões (se houver, ver Clip.mute_ranges)
    # também precisam ser remapeadas pros novos buracos — mesma lógica/motivo
    # do pan manual acima. O valor SALVO continua em coordenadas pré-corte.
    render_mute_ranges = remap_mute_ranges(clip.get_mute_ranges(), normalized)

    render_clip(
        clip,
        video,
        job,
        srt_segments=srt_segments,
        manual_pan_track=render_pan_track,
        cut_segments=normalized,
        mute_ranges=render_mute_ranges,
    )

    clip.set_cut_segments(normalized)
    clip.transcript_text = clip_text
    clip.hashtags, clip.suggested_titles = clip_rendering.compute_clip_metadata(clip_text)
    db.commit()
    db.refresh(clip)

    return _clip_to_out(clip)
