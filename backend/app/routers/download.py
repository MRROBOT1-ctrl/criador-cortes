"""Listagem de clipes gerados e download (individual ou ZIP com todos)."""
import json
import re
import shutil
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Clip, Job, Video
from app.schemas import ClipOut, JobSummary

router = APIRouter(tags=["download"])

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")
_RANGE_CHUNK_SIZE = 1024 * 1024  # 1MB por chunk


def _ranged_file_response(
    path: Path, request: Request, media_type: str = "video/mp4", filename: str | None = None,
) -> StreamingResponse | FileResponse:
    """Serve um arquivo respeitando o header `Range`, se presente.

    IMPORTANTE: `FileResponse` do Starlette (pelo menos até 0.38.x, versão
    usada aqui) NÃO implementa suporte a Range nenhum — sempre manda o
    arquivo inteiro, ignorando o header (bug real encontrado testando o
    scrub do editor: um vídeo de 1GB+ vinha inteiro em toda seek do
    `<video>`, o que travaria o navegador). Essa função implementa o 206
    Partial Content manualmente por cima de um `StreamingResponse`.
    """
    file_size = path.stat().st_size
    range_header = request.headers.get("range")

    if range_header is None:
        # Sem Range: manda o arquivo inteiro, mas avisa que sabe servir
        # ranges — é esse header que faz o navegador passar a pedir por
        # partes nas próximas requisições (seek do <video>, por exemplo).
        response = FileResponse(path, media_type=media_type, filename=filename)
        response.headers["Accept-Ranges"] = "bytes"
        return response

    match = _RANGE_RE.match(range_header)
    if not match:
        raise HTTPException(status_code=416, detail="Header Range inválido.")

    start_str, end_str = match.groups()
    start = int(start_str) if start_str else 0
    end = int(end_str) if end_str else file_size - 1
    end = min(end, file_size - 1)
    if start > end or start >= file_size:
        raise HTTPException(status_code=416, detail="Range fora dos limites do arquivo.")

    def iter_range():
        with path.open("rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = f.read(min(_RANGE_CHUNK_SIZE, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
    }
    if filename:
        headers["Content-Disposition"] = f'inline; filename="{filename}"'
    return StreamingResponse(iter_range(), status_code=206, media_type=media_type, headers=headers)


@router.get("/jobs", response_model=list[JobSummary])
def list_jobs(db: Session = Depends(get_db)):
    """Lista todos os jobs (mais recente primeiro) com um resumo pra tela
    inicial ("Meus cortes") — dados do vídeo e contagem de clipes já
    embutidos, sem o frontend precisar cruzar múltiplas chamadas."""
    jobs = db.query(Job).order_by(Job.created_at.desc()).all()
    return [
        JobSummary(
            id=job.id,
            status=job.status,
            progress=job.progress,
            current_phase=job.current_phase,
            created_at=job.created_at,
            video_filename=job.video.filename if job.video else "",
            video_duration=job.video.duration if job.video else None,
            clip_count=len(job.clips),
        )
        for job in jobs
    ]


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str, db: Session = Depends(get_db)):
    """Apaga um job (e o Video associado, que em cascata leva Clips e
    Metrics junto — ver relationships em app/models.py) e os arquivos em
    disco (uploads/outputs/data daquele job_id)."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job não encontrado.")

    video = db.get(Video, job.video_id)
    if video is not None:
        db.delete(video)  # cascata: Video -> Job -> Clip -> Metric
    else:
        db.delete(job)
    db.commit()

    for base_dir in (settings.upload_dir, settings.output_dir, settings.data_dir):
        job_dir = base_dir / job_id
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)

    return {"deleted": job_id}


def _clip_to_out(clip: Clip) -> ClipOut:
    # Não dá pra usar ClipOut.model_validate(clip) direto: o model guarda
    # `hashtags` como uma string JSON no banco, mas o schema espera um dict
    # já desserializado — a validação falharia antes de eu poder convertê-lo.
    return ClipOut(
        id=clip.id,
        video_id=clip.video_id,
        job_id=clip.job_id,
        index=clip.index,
        start_time=clip.start_time,
        end_time=clip.end_time,
        score=clip.score,
        processed_path=clip.processed_path,
        transcript_text=clip.transcript_text,
        hashtags=json.loads(clip.hashtags) if clip.hashtags else None,
        video_duration=clip.job.video.duration if clip.job and clip.job.video else None,
        has_streamer_region=bool(clip.job and clip.job.get_streamer_region()),
    )


@router.get("/jobs/{job_id}/original")
def get_original_video(job_id: str, request: Request, db: Session = Depends(get_db)):
    """Serve o vídeo ORIGINAL (não o clipe já cortado) — usado pelo editor
    do frontend pra permitir arrastar a linha do tempo e ver o frame real de
    qualquer ponto do vídeo, não só do trecho que já tinha sido cortado.
    Suporta `Range` de verdade (ver `_ranged_file_response` acima) — sem
    isso, cada seek do `<video>` baixaria o arquivo inteiro (alguns GB).

    Aviso: só reproduz de verdade no `<video>` do navegador se o container/
    codec for suportado (mp4/mov com H.264 tipicamente sim; avi/mkv com
    outros codecs podem não tocar — nesse caso o frontend cai num fallback
    sem prévia visual, só os campos numéricos de tempo)."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job não encontrado.")
    video = db.get(Video, job.video_id)
    if video is None or not video.filepath or not Path(video.filepath).exists():
        raise HTTPException(status_code=404, detail="Vídeo original não encontrado.")
    return _ranged_file_response(Path(video.filepath), request)


@router.get("/jobs/{job_id}/clips", response_model=list[ClipOut])
def list_clips(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job não encontrado.")
    clips = sorted(job.clips, key=lambda c: c.index)
    return [_clip_to_out(c) for c in clips]


@router.get("/clips/{clip_id}/file")
def download_clip(clip_id: str, request: Request, db: Session = Depends(get_db)):
    clip = db.get(Clip, clip_id)
    if clip is None or not clip.processed_path or not Path(clip.processed_path).exists():
        raise HTTPException(status_code=404, detail="Clipe não encontrado ou ainda não processado.")
    path = Path(clip.processed_path)
    return _ranged_file_response(path, request, filename=path.name)


@router.get("/jobs/{job_id}/download")
def download_all(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job não encontrado.")
    if job.status != "completed":
        raise HTTPException(status_code=409, detail=f"Job ainda não concluído (status={job.status}).")

    zip_path = settings.output_dir / job_id / "cortes.zip"
    clip_paths = [
        Path(clip.processed_path)
        for clip in sorted(job.clips, key=lambda c: c.index)
        if clip.processed_path and Path(clip.processed_path).exists()
    ]

    # Só reconstrói o zip se algum clipe for mais novo que ele (ex: depois de
    # um recut) — sem isso, todo clique em "baixar tudo" recomprimia todos os
    # clipes de novo mesmo sem nada ter mudado, e dois cliques/abas
    # simultâneos disputavam a escrita do mesmo arquivo.
    needs_rebuild = (
        not zip_path.exists()
        or any(p.stat().st_mtime > zip_path.stat().st_mtime for p in clip_paths)
    )
    if needs_rebuild:
        tmp_zip_path = zip_path.with_suffix(".tmp.zip")
        with zipfile.ZipFile(tmp_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for clip_path in clip_paths:
                zf.write(clip_path, arcname=clip_path.name)
        tmp_zip_path.replace(zip_path)  # troca atômica, evita zip parcial se duas requests colidirem

    return FileResponse(zip_path, media_type="application/zip", filename=f"cortes_{job_id}.zip")
