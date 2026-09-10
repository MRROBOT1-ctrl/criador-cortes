"""
Métricas de engajamento pós-publicação.

Lançamento manual funciona pra qualquer plataforma (TikTok, Reels, Shorts,
Facebook Reels). Sync automático só existe pro YouTube por enquanto — é a
única API que dá pra usar com só uma API key, sem app review (ver
app/services/youtube_metrics.py).
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Clip, Metric
from app.schemas import MetricIn, MetricOut, YoutubeSyncRequest
from app.services import youtube_metrics

router = APIRouter(tags=["metrics"])


@router.post("/clips/{clip_id}/metrics", response_model=MetricOut)
def add_metric(clip_id: str, payload: MetricIn, db: Session = Depends(get_db)):
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clipe não encontrado.")

    metric = Metric(clip_id=clip_id, source="manual", **payload.model_dump())
    db.add(metric)
    db.commit()
    db.refresh(metric)
    return metric


@router.get("/clips/{clip_id}/metrics", response_model=list[MetricOut])
def list_metrics(clip_id: str, db: Session = Depends(get_db)):
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clipe não encontrado.")
    return sorted(clip.metrics, key=lambda m: m.recorded_at)


@router.post("/clips/{clip_id}/metrics/youtube-sync", response_model=MetricOut)
def sync_youtube_metrics(clip_id: str, payload: YoutubeSyncRequest, db: Session = Depends(get_db)):
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clipe não encontrado.")

    try:
        stats = youtube_metrics.fetch_video_stats(payload.youtube_video_id)
    except youtube_metrics.YoutubeMetricsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    metric = Metric(
        clip_id=clip_id,
        platform="youtube_shorts",
        source="youtube_api",
        external_video_id=payload.youtube_video_id,
        **stats,
    )
    db.add(metric)
    db.commit()
    db.refresh(metric)
    return metric
