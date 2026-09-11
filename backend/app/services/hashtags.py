"""
Geração de hashtags sugeridas por corte, por plataforma.

MVP: combina palavras-chave extraídas do texto transcrito do trecho (ver
app/services/keywords.py) com um pool de tags genéricas por plataforma. Não é
dado de tendência em tempo real (exigiria integração com API de cada rede) —
é um ponto de partida editável pelo usuário antes de publicar.
"""
from app.services.keywords import extract_keywords

GENERIC_TAGS: dict[str, list[str]] = {
    "tiktok": ["#fyp", "#foryoupage", "#viral", "#trending", "#tiktokbrasil"],
    "instagram_reels": ["#reels", "#reelsbrasil", "#explorar", "#instagood", "#viral"],
    "youtube_shorts": ["#shorts", "#youtubeshorts", "#viral", "#trending"],
    "facebook_reels": ["#reels", "#facebookreels", "#viral"],
}


def generate_hashtags(text: str) -> dict[str, list[str]]:
    """Gera hashtags para as 4 plataformas suportadas a partir do texto do corte."""
    keywords = extract_keywords(text or "")
    keyword_tags = [f"#{kw}" for kw in keywords]

    return {
        platform: keyword_tags + generic
        for platform, generic in GENERIC_TAGS.items()
    }
