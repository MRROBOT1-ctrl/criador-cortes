"""
Biblioteca de vídeos "relaxantes" de fundo (parkour/slime/satisfying etc.) —
usados como fallback visual em clipes de narração pura, sem streamer nem
rosto rastreável (ver app/services/video_cutter.py, `relaxing_video_path`, e
Job.use_relaxing_video_fallback/relaxing_video_id em app/models.py).

Baixados via yt-dlp a partir de um link, em BACKGROUND via Celery (ver
app/tasks/relaxing_videos_pipeline.py) — POST cria o registro (status=
"downloading") e retorna na hora, sem esperar o yt-dlp terminar (um vídeo de
30min travando a request por minutos estourava timeout de proxy e dava a
entender pro frontend que a request tinha quebrado). O cliente faz polling em
GET /relaxing-videos/{id} até o status virar "ready" ou "failed".
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import RelaxingVideo
from app.tasks.relaxing_videos_pipeline import download_relaxing_video

router = APIRouter(tags=["relaxing_videos"])


class RelaxingVideoAddRequest(BaseModel):
    url: str = Field(description="Link do vídeo (YouTube/TikTok/etc, baixado via yt-dlp).")


class RelaxingVideoOut(BaseModel):
    id: str
    title: str | None
    duration: float | None
    source_url: str | None
    status: str  # downloading | ready | failed
    error_message: str | None

    class Config:
        from_attributes = True


@router.get("/relaxing-videos", response_model=list[RelaxingVideoOut])
def list_relaxing_videos(db: Session = Depends(get_db)):
    videos = db.query(RelaxingVideo).order_by(RelaxingVideo.created_at.desc()).all()
    return videos


@router.get("/relaxing-videos/{video_id}", response_model=RelaxingVideoOut)
def get_relaxing_video(video_id: str, db: Session = Depends(get_db)):
    """Consulta o status de UM vídeo relaxante — usado pelo frontend pra
    fazer polling depois do POST (que retorna antes do download terminar)."""
    video = db.get(RelaxingVideo, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Vídeo relaxante não encontrado.")
    return video


@router.post("/relaxing-videos", response_model=RelaxingVideoOut, status_code=202)
def add_relaxing_video(payload: RelaxingVideoAddRequest, db: Session = Depends(get_db)):
    """Cria o registro (status="downloading", filepath ainda vazio) e
    dispara o download em background — não espera o yt-dlp terminar. 202
    (Accepted) em vez do 200 de sempre: a resposta não representa mais o
    recurso PRONTO, representa "aceito, processando" (ver GET pra polling)."""
    video = RelaxingVideo(source_url=payload.url, filepath="", status="downloading")
    db.add(video)
    db.commit()
    db.refresh(video)

    download_relaxing_video.delay(video.id)

    return video


@router.delete("/relaxing-videos/{video_id}")
def delete_relaxing_video(video_id: str, db: Session = Depends(get_db)):
    """Apaga o registro (e o arquivo em disco, se já tiver sido baixado).

    Funciona em qualquer status, inclusive "downloading" (a task Celery pode
    estar rodando nesse exato momento): `video.filepath` continua "" até o
    download terminar de verdade, então o `if` abaixo naturalmente só apaga
    o REGISTRO nesse caso (nada pra desligar no arquivo — ele ainda não
    existe). Se a task estiver no meio do download quando esse DELETE
    commitar, ela detecta o registro sumido (reconsulta o banco, não confia
    em cache — ver _get_fresh em app/tasks/relaxing_videos_pipeline.py) e
    aborta sozinha sem crashar, inclusive limpando o arquivo se o download
    já tiver terminado bem nesse meio-tempo (ver mesmo módulo)."""
    video = db.get(RelaxingVideo, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Vídeo relaxante não encontrado.")

    # Jobs que já escolheram esse vídeo (Job.relaxing_video_id) ficam com uma
    # referência solta — tolerado de propósito (mesmo espírito de
    # find_watermark_file: resolve_job_render_defaults confere existência do
    # arquivo antes de usar, não quebra um recut só porque o vídeo relaxante
    # escolhido originalmente foi removido da biblioteca depois).
    if video.filepath and Path(video.filepath).exists():
        Path(video.filepath).unlink()
    db.delete(video)
    db.commit()
    return {"deleted": video_id}
