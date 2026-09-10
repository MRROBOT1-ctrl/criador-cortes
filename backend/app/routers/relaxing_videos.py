"""
Biblioteca de vídeos "relaxantes" de fundo (parkour/slime/satisfying etc.) —
usados como fallback visual em clipes de narração pura, sem streamer nem
rosto rastreável (ver app/services/video_cutter.py, `relaxing_video_path`, e
Job.use_relaxing_video_fallback/relaxing_video_id em app/models.py).

Baixados via yt-dlp a partir de um link, mesmo mecanismo já usado pro
upload por link do vídeo principal (ver app/services/video_downloader.py).
"""
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import RelaxingVideo
from app.services import video_downloader
from app.services.video_cutter import FFmpegError, probe_duration

router = APIRouter(tags=["relaxing_videos"])


class RelaxingVideoAddRequest(BaseModel):
    url: str = Field(description="Link do vídeo (YouTube/TikTok/etc, baixado via yt-dlp).")


class RelaxingVideoOut(BaseModel):
    id: str
    title: str | None
    duration: float | None
    source_url: str | None

    class Config:
        from_attributes = True


@router.get("/relaxing-videos", response_model=list[RelaxingVideoOut])
def list_relaxing_videos(db: Session = Depends(get_db)):
    videos = db.query(RelaxingVideo).order_by(RelaxingVideo.created_at.desc()).all()
    return videos


@router.post("/relaxing-videos", response_model=RelaxingVideoOut)
def add_relaxing_video(payload: RelaxingVideoAddRequest, db: Session = Depends(get_db)):
    # filepath="" (não None) só pra passar pela constraint NOT NULL até o
    # download terminar e a gente saber o caminho de verdade — mesmo truque
    # já usado em routers/upload.py::upload_video.
    video = RelaxingVideo(source_url=payload.url, filepath="")
    db.add(video)
    db.flush()  # gera o id sem commitar ainda, pra usar no nome do arquivo

    dest_dir = settings.relaxing_videos_dir
    tmp_dir = dest_dir / video.id
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        downloaded_path, title = video_downloader.download_video(payload.url, tmp_dir)
    except video_downloader.VideoDownloadError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # `download_video` baixa pra uma pasta própria (dest_dir/{id}/original.*)
    # — mesma convenção do vídeo principal, reaproveitada sem modificar o
    # helper. Move pra um nome mais simples direto em relaxing_videos/{id}.mp4
    # (não tem "job" aqui, não precisa da subpasta).
    final_path = dest_dir / f"{video.id}{downloaded_path.suffix}"
    shutil.move(str(downloaded_path), str(final_path))
    downloaded_path.parent.rmdir()  # pasta temporária agora vazia

    try:
        duration = probe_duration(final_path)
    except FFmpegError:
        duration = None

    video.filepath = str(final_path)
    video.title = title
    video.duration = duration
    db.commit()
    db.refresh(video)
    return video


@router.delete("/relaxing-videos/{video_id}")
def delete_relaxing_video(video_id: str, db: Session = Depends(get_db)):
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
