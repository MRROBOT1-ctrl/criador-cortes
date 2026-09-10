"""
Sync automático de métricas via YouTube Data API v3.

Só precisa de uma API key simples (sem OAuth, sem app review) — por isso foi
a única plataforma escolhida pra automação no MVP. TikTok/Instagram exigiriam
criar um app de desenvolvedor e passar por aprovação da Meta/TikTok, então
por ora ficam com lançamento manual (ver MetricIn na API).
"""
import httpx

from app.config import settings


class YoutubeMetricsError(RuntimeError):
    pass


def fetch_video_stats(youtube_video_id: str) -> dict:
    """Busca views/likes/comments atuais de um vídeo público do YouTube."""
    if not settings.youtube_api_key:
        raise YoutubeMetricsError(
            "YOUTUBE_API_KEY não configurada — defina no .env pra habilitar o sync automático."
        )

    response = httpx.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={
            "part": "statistics",
            "id": youtube_video_id,
            "key": settings.youtube_api_key,
        },
        timeout=10.0,
    )
    response.raise_for_status()
    data = response.json()

    items = data.get("items", [])
    if not items:
        raise YoutubeMetricsError(f"Vídeo '{youtube_video_id}' não encontrado (privado, removido ou ID errado).")

    stats = items[0]["statistics"]
    return {
        "views": int(stats.get("viewCount", 0)),
        "likes": int(stats.get("likeCount", 0)),
        "comments": int(stats.get("commentCount", 0)),
        "shares": 0,  # a API do YouTube não expõe contagem de compartilhamentos
    }
