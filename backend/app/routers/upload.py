"""Endpoint de upload de vídeo — cria Video + Job e dispara o pipeline no Celery."""
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Job, Video
from app.schemas import (
    UploadResponse,
    UrlUploadRequest,
    validate_caption_color,
    validate_caption_font_family,
    validate_caption_style,
    validate_profanity_filter,
)
from app.tasks.pipeline import process_video

router = APIRouter(tags=["upload"])

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv"}
WATERMARK_POSITIONS = {"top-left", "top-right", "bottom-left", "bottom-right"}
# Faixa recalibrada: o FontSize do libass numa legenda SRT sem PlayRes
# escala bem maior do que o número sugere (20, o default antigo, já ocupava
# ~35% da altura do quadro — usuário reportou "legenda muito grande").
CAPTION_FONT_SIZE_MIN = 6
CAPTION_FONT_SIZE_MAX = 20


def _validate_caption_options(
    caption_font_size: int, caption_color: str, caption_style: str, caption_font_family: str,
) -> None:
    """Validação compartilhada dos campos de estilo de legenda — mesmo
    padrão usado acima pra watermark_position: 400 com mensagem clara em vez
    de deixar passar silenciosamente (ou estourar um 500 lá na frente)."""
    if not (CAPTION_FONT_SIZE_MIN <= caption_font_size <= CAPTION_FONT_SIZE_MAX):
        raise HTTPException(
            status_code=400,
            detail=f"caption_font_size inválido ({caption_font_size}). Use um valor entre "
            f"{CAPTION_FONT_SIZE_MIN} e {CAPTION_FONT_SIZE_MAX}.",
        )
    try:
        validate_caption_color(caption_color)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        validate_caption_style(caption_style)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        validate_caption_font_family(caption_font_family)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _validate_profanity_filter(value: str) -> None:
    try:
        validate_profanity_filter(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _validate_clip_duration(clip_duration_min: int, clip_duration_max: int) -> None:
    """O formulário React já confere isso no cliente, mas uma chamada direta
    à API (curl, outro cliente) passava direto sem checagem nenhuma — o
    resultado era um corte mais curto que o mínimo configurado, sem erro
    nenhum (audio_analysis.pick_top_clips centraliza no target_duration e
    depois recorta pro clip_duration_max, que "vence" silenciosamente se for
    menor que o mínimo)."""
    if clip_duration_max <= clip_duration_min:
        raise HTTPException(
            status_code=400,
            detail=f"clip_duration_max ({clip_duration_max}) deve ser maior que "
            f"clip_duration_min ({clip_duration_min}).",
        )


@router.post("/upload", response_model=UploadResponse)
async def upload_video(
    file: UploadFile = File(...),
    max_clips: int = Form(default=settings.default_max_clips),
    clip_duration_min: int = Form(default=settings.default_clip_duration_min),
    clip_duration_max: int = Form(default=settings.default_clip_duration_max),
    keywords: str = Form(default=""),
    language: str | None = Form(default=None),
    watermark_enabled: bool = Form(default=False),
    watermark_position: str = Form(default="bottom-right"),
    captions_enabled: bool = Form(default=True),
    caption_font_size: int = Form(default=8),
    caption_color: str = Form(default="#FFFFFF"),
    caption_style: str = Form(default="bold_outline"),
    caption_font_family: str = Form(default="DejaVu Sans"),
    use_relaxing_video_fallback: bool = Form(default=True),
    profanity_filter: str = Form(default="off"),
    db: Session = Depends(get_db),
):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Formato '{ext}' não suportado. Use: {', '.join(sorted(ALLOWED_EXTENSIONS))}.",
        )
    if watermark_position not in WATERMARK_POSITIONS:
        raise HTTPException(
            status_code=400,
            detail=f"watermark_position inválida. Use: {', '.join(sorted(WATERMARK_POSITIONS))}.",
        )
    _validate_caption_options(caption_font_size, caption_color, caption_style, caption_font_family)
    _validate_clip_duration(clip_duration_min, clip_duration_max)
    _validate_profanity_filter(profanity_filter)

    video = Video(filename=file.filename, filepath="", status="uploaded")
    db.add(video)
    db.flush()  # gera o video.id sem commitar ainda

    job = Job(
        id=video.id,  # pasta uploads/{job_id} == id do job; usamos o id do video como job_id inicial
        video_id=video.id,
        status="pending",
        max_clips=max_clips,
        clip_duration_min=clip_duration_min,
        clip_duration_max=clip_duration_max,
        keywords=keywords,
        language=language or None,
        watermark_enabled=watermark_enabled,
        watermark_position=watermark_position,
        captions_enabled=captions_enabled,
        caption_font_size=caption_font_size,
        caption_color=caption_color,
        caption_style=caption_style,
        caption_font_family=caption_font_family,
        use_relaxing_video_fallback=use_relaxing_video_fallback,
        profanity_filter=profanity_filter,
    )
    db.add(job)

    job_dir = settings.upload_dir / job.id
    job_dir.mkdir(parents=True, exist_ok=True)
    dest_path = job_dir / f"original{ext}"

    max_bytes = int(settings.max_upload_size_gb * 1024 ** 3)
    size = 0
    with dest_path.open("wb") as out_file:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                out_file.close()
                dest_path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"Arquivo maior que o limite de {settings.max_upload_size_gb}GB.",
                )
            out_file.write(chunk)

    video.filepath = str(dest_path)
    job.add_log(f"Upload recebido: {file.filename} ({size / (1024 ** 2):.1f} MB).")
    db.commit()
    db.refresh(video)
    db.refresh(job)

    process_video.delay(job.id)

    return UploadResponse(video=video, job=job)


@router.post("/upload-url", response_model=UploadResponse)
def upload_from_url(payload: UrlUploadRequest, db: Session = Depends(get_db)):
    """Alternativa ao upload de arquivo: recebe um link (YouTube, TikTok,
    Instagram etc.) e baixa o vídeo via yt-dlp dentro do próprio worker
    Celery (fase "downloading", ver app/tasks/pipeline.py) — a request HTTP
    só cria o registro e dispara o job, não espera o download terminar."""
    url = payload.url.strip()
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL inválida — precisa começar com http:// ou https://.")
    if payload.watermark_position not in WATERMARK_POSITIONS:
        raise HTTPException(
            status_code=400,
            detail=f"watermark_position inválida. Use: {', '.join(sorted(WATERMARK_POSITIONS))}.",
        )
    _validate_caption_options(
        payload.caption_font_size, payload.caption_color, payload.caption_style, payload.caption_font_family,
    )
    _validate_clip_duration(payload.clip_duration_min, payload.clip_duration_max)
    _validate_profanity_filter(payload.profanity_filter)

    # filename/filepath ficam provisórios até o worker baixar o vídeo de
    # verdade e descobrir o título real (ver fase "downloading" do pipeline).
    video = Video(filename=url, filepath="", status="downloading", source_url=url)
    db.add(video)
    db.flush()

    job = Job(
        id=video.id,
        video_id=video.id,
        status="pending",
        max_clips=payload.max_clips,
        clip_duration_min=payload.clip_duration_min,
        clip_duration_max=payload.clip_duration_max,
        keywords=payload.keywords,
        language=payload.language or None,
        watermark_enabled=payload.watermark_enabled,
        watermark_position=payload.watermark_position,
        captions_enabled=payload.captions_enabled,
        caption_font_size=payload.caption_font_size,
        caption_color=payload.caption_color,
        caption_style=payload.caption_style,
        caption_font_family=payload.caption_font_family,
        use_relaxing_video_fallback=payload.use_relaxing_video_fallback,
        profanity_filter=payload.profanity_filter,
    )
    db.add(job)

    job_dir = settings.upload_dir / job.id
    job_dir.mkdir(parents=True, exist_ok=True)

    job.add_log(f"Link recebido: {url}")
    db.commit()
    db.refresh(video)
    db.refresh(job)

    process_video.delay(job.id)

    return UploadResponse(video=video, job=job)
