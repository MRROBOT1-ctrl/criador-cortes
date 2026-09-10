"""
Geração de hashtags sugeridas por corte, por plataforma.

MVP: combina palavras-chave extraídas do texto transcrito do trecho com um
pool de tags genéricas por plataforma. Não é dado de tendência em tempo real
(exigiria integração com API de cada rede) — é um ponto de partida editável
pelo usuário antes de publicar.
"""
import re
from collections import Counter

# Stopwords básicas em português (PT-BR) — suficiente pro MVP, não é uma lista exaustiva.
_STOPWORDS = {
    "a", "o", "os", "as", "de", "da", "do", "das", "dos", "e", "é", "em", "um", "uma",
    "que", "com", "não", "para", "por", "se", "na", "no", "nas", "nos", "mas", "ao",
    "aos", "à", "às", "isso", "isso", "esse", "essa", "este", "esta", "eu", "tu", "ele",
    "ela", "nós", "vocês", "eles", "elas", "muito", "mais", "menos", "já", "só", "também",
    "vai", "foi", "ser", "tá", "aí", "então", "assim", "como", "quando", "onde", "porque",
}

GENERIC_TAGS: dict[str, list[str]] = {
    "tiktok": ["#fyp", "#foryoupage", "#viral", "#trending", "#tiktokbrasil"],
    "instagram_reels": ["#reels", "#reelsbrasil", "#explorar", "#instagood", "#viral"],
    "youtube_shorts": ["#shorts", "#youtubeshorts", "#viral", "#trending"],
    "facebook_reels": ["#reels", "#facebookreels", "#viral"],
}


def _extract_keywords(text: str, max_keywords: int = 5) -> list[str]:
    # Captura palavras hifenizadas como um token só (ex: "bem-vindos"), evitando
    # que o hífen quebre a palavra em pedaços separados.
    words = re.findall(r"[a-zA-ZÀ-ÿ]+(?:-[a-zA-ZÀ-ÿ]+)*", text.lower())
    # Filtro de tamanho mínimo (>=4 caracteres, ignorando hífens na contagem).
    words = [w for w in words if len(w.replace("-", "")) >= 4]
    words = [w for w in words if w not in _STOPWORDS]
    if not words:
        return []
    most_common = Counter(words).most_common(max_keywords)
    return [word for word, _count in most_common]


def generate_hashtags(text: str) -> dict[str, list[str]]:
    """Gera hashtags para as 4 plataformas suportadas a partir do texto do corte."""
    keywords = _extract_keywords(text or "")
    keyword_tags = [f"#{kw}" for kw in keywords]

    return {
        platform: keyword_tags + generic
        for platform, generic in GENERIC_TAGS.items()
    }
