"""
Task Celery que baixa um vídeo "relaxante" (ver app/routers/relaxing_videos.py)
em background. POST /relaxing-videos só cria o registro (status="downloading")
e dispara essa task, sem esperar o yt-dlp terminar — igual ao padrão já usado
pro vídeo principal via link (ver app/tasks/pipeline.py, fase "downloading").
"""
import logging
import shutil

from app.celery_app import celery_app
from app.config import settings
from app.database import SessionLocal
from app.models import RelaxingVideo
from app.services import video_downloader
from app.services.video_cutter import FFmpegError, probe_duration

logger = logging.getLogger(__name__)

# yt-dlp (ou qualquer exceção inesperada) pode jogar uma mensagem gigante
# (stacktrace, corpo de erro HTML de uma página de vídeo indisponível etc.)
# — trunca antes de salvar, tanto por UX (ninguém lê um erro de 10k
# caracteres) quanto pra não deixar o banco com um campo enorme à toa.
_ERROR_MESSAGE_MAX_LEN = 2000


def _truncate_error(exc: Exception) -> str:
    text = str(exc)
    if len(text) <= _ERROR_MESSAGE_MAX_LEN:
        return text
    return text[:_ERROR_MESSAGE_MAX_LEN] + "... (truncado)"


def _get_fresh(db, video_id: str) -> RelaxingVideo | None:
    """Busca o registro SEMPRE com uma query de verdade no banco (nunca
    `db.get()`) — importante aqui porque essa task é de longa duração
    (minutos, um download) e reconsulta o MESMO id várias vezes na MESMA
    session: `db.get()` pode devolver o objeto já em cache no identity map
    da session sem checar o banco de novo, então NÃO perceberia um DELETE
    /relaxing-videos/{id} feito por outra request enquanto o download roda.
    Uma query (`.filter(...).first()`) sempre bate no banco de verdade."""
    return db.query(RelaxingVideo).filter(RelaxingVideo.id == video_id).first()


@celery_app.task(name="relaxing_videos.download", bind=True)
def download_relaxing_video(self, video_id: str) -> dict:
    """Baixa o vídeo relaxante via yt-dlp e atualiza o registro pra
    status="ready" (ou "failed" + error_message). NUNCA relança a exceção —
    com broker Redis + task_acks_late=True (ver app/celery_app.py), uma task
    que levanta exceção é reentregue e reprocessada, o que aqui viraria um
    loop de retry infinito pra um link que não baixa mesmo. O dict retornado
    só serve pra aparecer no log/resultado da task (celery events/flower),
    ninguém consome esse retorno.

    Checa se o registro ainda existe (ver _get_fresh) em CADA etapa que pode
    ter demorado o suficiente pra um DELETE concorrente ter acontecido no
    meio — se sumiu, aborta limpo (sem crashar, sem ressuscitar o registro,
    sem deixar arquivo órfão em disco)."""
    db = SessionLocal()
    try:
        video = _get_fresh(db, video_id)
        if video is None:
            logger.info("RelaxingVideo %s não existe mais (apagado antes da task começar) — abortando.", video_id)
            return {"video_id": video_id, "status": "skipped", "reason": "deleted_before_start"}

        source_url = video.source_url
        dest_dir = settings.relaxing_videos_dir
        tmp_dir = dest_dir / video_id
        tmp_dir.mkdir(parents=True, exist_ok=True)

        try:
            downloaded_path, title = video_downloader.download_video(source_url, tmp_dir)
        except video_downloader.VideoDownloadError as exc:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            video = _get_fresh(db, video_id)
            if video is None:
                logger.info(
                    "RelaxingVideo %s foi apagado durante o download (que falhou) — nada a atualizar.", video_id
                )
                return {"video_id": video_id, "status": "skipped", "reason": "deleted_during_download"}
            video.status = "failed"
            video.error_message = _truncate_error(exc)
            db.commit()
            logger.warning("Download do vídeo relaxante %s falhou: %s", video_id, exc)
            return {"video_id": video_id, "status": "failed", "error": video.error_message}

        # Mesma convenção do router antigo: move pra um nome simples direto
        # em relaxing_videos/{id}.ext (não tem "job" aqui, não precisa de
        # subpasta) — a pasta temporária de download fica vazia e é removida.
        final_path = dest_dir / f"{video_id}{downloaded_path.suffix}"
        shutil.move(str(downloaded_path), str(final_path))
        downloaded_path.parent.rmdir()

        try:
            duration = probe_duration(final_path)
        except FFmpegError:
            duration = None

        video = _get_fresh(db, video_id)
        if video is None:
            # Apagado ENQUANTO o download rodava (esse deu certo) — o arquivo
            # baixado ficaria órfão em disco pra sempre se não limpar aqui.
            final_path.unlink(missing_ok=True)
            logger.info(
                "RelaxingVideo %s foi apagado durante o download (que deu certo) — descartando arquivo baixado.",
                video_id,
            )
            return {"video_id": video_id, "status": "skipped", "reason": "deleted_during_download"}

        video.filepath = str(final_path)
        video.title = title
        video.duration = duration
        video.status = "ready"
        db.commit()
        logger.info("Download do vídeo relaxante %s concluído (\"%s\").", video_id, title)
        return {"video_id": video_id, "status": "ready", "title": title, "duration": duration}

    except Exception as exc:  # noqa: BLE001 — nunca relança, ver docstring acima
        logger.exception("Falha inesperada baixando vídeo relaxante %s", video_id)
        try:
            video = _get_fresh(db, video_id)
            if video is not None:
                video.status = "failed"
                video.error_message = _truncate_error(exc)
                db.commit()
        except Exception:  # noqa: BLE001 — nem isso pode derrubar a task
            logger.exception("Falha ao marcar RelaxingVideo %s como failed", video_id)
        return {"video_id": video_id, "status": "failed", "error": _truncate_error(exc)}
    finally:
        db.close()
