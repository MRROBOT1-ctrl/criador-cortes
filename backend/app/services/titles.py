"""
Títulos sugestivos por corte — mesmo espírito/limitação de hashtags.py: MVP
por template + palavra-chave extraída do texto transcrito, NÃO é geração via
modelo de linguagem (não há chamada a nenhuma IA externa aqui). Serve como
ponto de partida editável pelo usuário antes de publicar, não como título
final pronto.
"""
from app.services.keywords import extract_keywords

# Templates genéricos de "gancho" (clickbait moderado) pra vídeos curtos —
# cada um recebe UMA palavra-chave do trecho. Ordem importa pouco (rotaciona
# entre as keywords disponíveis), mas a lista fica em ordem do mais genérico
# pro mais específico.
TITLE_TEMPLATES = [
    "Você não vai acreditar no que aconteceu com {kw}",
    "{kw} deixou todo mundo chocado",
    "O momento em que {kw} virou tudo",
    "Ninguém esperava isso: {kw}",
    "{kw}?? Olha só isso",
    "A cena de {kw} que viralizou",
]


def generate_titles(text: str, max_titles: int = 5) -> list[str]:
    """Gera até `max_titles` títulos sugeridos a partir do texto transcrito
    do corte. Lista vazia se não sobrar nenhuma palavra-chave (trecho sem
    fala, só stopwords, etc.) — mesmo comportamento de generate_hashtags
    nesse caso."""
    keywords = extract_keywords(text or "", max_keywords=3)
    if not keywords:
        return []

    titles: list[str] = []
    for i, template in enumerate(TITLE_TEMPLATES):
        if len(titles) >= max_titles:
            break
        kw = keywords[i % len(keywords)].capitalize()
        titles.append(template.format(kw=kw))
    return titles
