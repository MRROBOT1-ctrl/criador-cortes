"""
Download de vídeo a partir de um link (YouTube, TikTok, Instagram etc.) via
yt-dlp — usado no fluxo "colar link" como alternativa ao upload de arquivo.
"""
import logging
from pathlib import Path
from typing import Callable

import yt_dlp

from app.config import settings

logger = logging.getLogger(__name__)


class VideoDownloadError(RuntimeError):
    pass


def download_video(
    url: str,
    dest_dir: Path,
    on_progress: Callable[[float], None] | None = None,
) -> tuple[Path, str | None]:
    """Baixa o vídeo da URL para `dest_dir/original.mp4` e retorna
    (caminho_do_arquivo, título_ou_None).

    `merge_output_format=mp4` garante que o resultado final é sempre um
    .mp4, mesmo quando o yt-dlp baixa vídeo e áudio como faixas separadas
    e precisa mixá-los (comum em formatos "adaptativos" do YouTube) — assim
    o resto do pipeline não precisa lidar com extensões variáveis.

    `on_progress`, se fornecido, é chamado com o percentual (0-100) do
    download em andamento — sem isso o job fica com o mesmo progresso fixo
    do início ao fim da fase de download, o que parece "travado" pro usuário
    num vídeo grande/live longa (yt-dlp dispara o hook várias vezes por
    segundo, então o chamador deve filtrar/throttle antes de escrever no
    banco a cada chamada).
    """
    outtmpl = str(dest_dir / "original.%(ext)s")
    max_bytes = int(settings.max_upload_size_gb * 1024 ** 3)

    def _hook(d: dict) -> None:
        if on_progress is None or d.get("status") != "downloading":
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        # "downloaded_bytes" pode estar ausente/None num hook intermediário
        # mesmo com total_bytes já preenchido (visto em downloads com
        # formatos separados de vídeo+áudio sendo mesclados) — sem o default,
        # "downloaded / total" levanta TypeError dentro do hook, que o
        # yt-dlp propaga e pode abortar o download de uma VOD/live longa.
        downloaded = d.get("downloaded_bytes") or 0
        if total:
            on_progress(min(99.0, downloaded / total * 100))

    ydl_opts = {
        "outtmpl": outtmpl,
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best",
        "merge_output_format": "mp4",
        "max_filesize": max_bytes,
        "noplaylist": True,  # baixa só o vídeo do link, nunca a playlist inteira
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "progress_hooks": [_hook],
    }

    logger.info("Baixando vídeo de %s via yt-dlp...", url)
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        raise VideoDownloadError(f"Não foi possível baixar o vídeo desse link: {exc}") from exc

    matches = sorted(dest_dir.glob("original.*"))
    if not matches:
        raise VideoDownloadError("O download terminou mas o arquivo não apareceu no disco — link inválido?")

    title = info.get("title") if isinstance(info, dict) else None
    return matches[0], title
