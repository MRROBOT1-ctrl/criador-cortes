"""
Extração simples de palavras-chave de um texto transcrito — usada tanto por
hashtags.py quanto por titles.py (extraída daqui pra não duplicar a mesma
lista de stopwords/regex nos dois lugares).
"""
import re
from collections import Counter

# Stopwords básicas em português (PT-BR) — suficiente pro MVP, não é uma lista exaustiva.
STOPWORDS = {
    "a", "o", "os", "as", "de", "da", "do", "das", "dos", "e", "é", "em", "um", "uma",
    "que", "com", "não", "para", "por", "se", "na", "no", "nas", "nos", "mas", "ao",
    "aos", "à", "às", "isso", "isso", "esse", "essa", "este", "esta", "eu", "tu", "ele",
    "ela", "nós", "vocês", "eles", "elas", "muito", "mais", "menos", "já", "só", "também",
    "vai", "foi", "ser", "tá", "aí", "então", "assim", "como", "quando", "onde", "porque",
}


def extract_keywords(text: str, max_keywords: int = 5) -> list[str]:
    # Captura palavras hifenizadas como um token só (ex: "bem-vindos"), evitando
    # que o hífen quebre a palavra em pedaços separados.
    words = re.findall(r"[a-zA-ZÀ-ÿ]+(?:-[a-zA-ZÀ-ÿ]+)*", (text or "").lower())
    # Filtro de tamanho mínimo (>=4 caracteres, ignorando hífens na contagem).
    words = [w for w in words if len(w.replace("-", "")) >= 4]
    words = [w for w in words if w not in STOPWORDS]
    if not words:
        return []
    most_common = Counter(words).most_common(max_keywords)
    return [word for word, _count in most_common]
