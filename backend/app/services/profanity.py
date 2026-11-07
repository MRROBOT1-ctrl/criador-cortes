"""
Filtro de palavrões (PT-BR) — best-effort por lista fixa de palavras, sem
nenhum modelo/API externa. Dois modos (ver Job.profanity_filter):

- "cut": remove o trecho da palavra ofensiva do vídeo final (reaproveita o
  mesmo mecanismo de "cortes internos" já usado por app/routers/clip_cuts.py
  — ver app/tasks/pipeline.py).
- "censor": mantém o vídeo intacto, mas MUTA o áudio exatamente na janela da
  palavra (ver `mute_ranges` em app/services/video_cutter.py) e troca a
  palavra por asteriscos na legenda queimada (`redact_text` abaixo).

Precisão limitada por natureza: depende da palavra ter sido transcrita
corretamente pelo Whisper (erro de transcrição = passa direto) e da lista
abaixo cobrir a variação usada (sem flexão automática além do que já está
listado). Nunca derruba o job por causa disso — ver chamadas em pipeline.py.
"""
import re
import unicodedata

from app.services.transcription import Segment, Word

# Lista básica e deliberadamente conservadora (termos de baixo calão comuns
# em PT-BR) — não é exaustiva, é um ponto de partida. Comparação ignora
# acento/caixa (ver _normalize), então cada entrada já cobre as variações
# combinadas com isso (plural/gênero explícitos quando mudam a grafia).
PROFANITY_WORDS: set[str] = {
    "porra", "caralho", "caralhos", "merda", "merdas", "puta", "putas", "putaria",
    "puto", "putos", "foda", "fodase", "fodasse", "fuder", "fodido", "fodida",
    "cacete", "bosta", "bostas", "cuzao", "cuzo", "cuzoes", "arrombado", "arrombada",
    "desgraca", "desgracado", "desgracada", "viado", "viadinho", "corno", "corna",
    "cornao", "piranha", "piranhas", "vagabundo", "vagabunda", "otario", "otaria",
    "imbecil", "idiota", "retardado", "retardada", "buceta", "bucetas", "pau",
    "pica", "picas", "xoxota", "rola", "punheta", "punheteiro", "boquete",
    "escroto", "escrota", "babaca", "cacetada",
}


def _normalize(word: str) -> str:
    """minúsculas + sem acento + só letras — mesma normalização usada tanto
    pra montar PROFANITY_WORDS (implícito, já sem acento) quanto pra comparar
    contra ela."""
    decomposed = unicodedata.normalize("NFKD", word.lower())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z]", "", stripped)


def is_profane(word: str) -> bool:
    return _normalize(word) in PROFANITY_WORDS


_WORD_RE = re.compile(r"[a-zA-ZÀ-ÿ]+")


def redact_text(text: str) -> str:
    """Substitui cada palavra ofensiva encontrada em `text` por asteriscos do
    mesmo tamanho (ex: "caralho" -> "*******") — usado na legenda queimada
    no modo "censor". Comparação por palavra inteira (regex `\\b`-like via
    _WORD_RE), não substring — não afeta palavras que só CONTÊM uma
    ofensiva como substring incidental."""
    def _replace(match: re.Match) -> str:
        word = match.group(0)
        return "*" * len(word) if is_profane(word) else word

    return _WORD_RE.sub(_replace, text or "")


def redact_segments(segments: list[Segment]) -> list[Segment]:
    """Aplica `redact_text` no texto de cada segmento (tempos e `words`
    intactos). Existe porque o transcript.json guarda o texto BRUTO do
    Whisper e, fora do pipeline inicial, quem reconstrói a legenda (o
    PUT /clips/{id}/edit, o GET /draft e o recut, via app/services/
    clip_edit.py) precisa reaplicar a censura sempre que o filtro estiver
    ligado. Só serve pra falas do Whisper: texto digitado pelo usuário
    (legenda em modo manual) NUNCA passa por aqui, porque é intencional."""
    return [
        Segment(start=seg.start, end=seg.end, text=redact_text(seg.text), words=seg.words)
        for seg in segments
    ]


def find_profanity_words(words: list[Word]) -> list[Word]:
    """Filtra, de uma lista de palavras com timestamp (ver Segment.words),
    só as que batem contra PROFANITY_WORDS."""
    return [w for w in words if w.text and is_profane(w.text)]


def find_profanity_spans_in_range(
    segments: list[Segment], start: float, end: float, padding: float = 0.05,
) -> list[tuple[float, float]]:
    """Varre os segmentos que se sobrepõem a [start, end] (tempos ABSOLUTOS
    no vídeo original) e devolve os intervalos (também ABSOLUTOS) de cada
    palavra ofensiva encontrada, com uma pequena folga (`padding`) de cada
    lado — evita cortar/mutar rente demais e comer o fim da palavra anterior/
    início da próxima. Intervalos já ordenados e SEM merge (quem usa decide
    se precisa mesclar sobreposições, ver pipeline.py)."""
    spans: list[tuple[float, float]] = []
    for seg in segments:
        if seg.end < start or seg.start > end:
            continue
        for word in find_profanity_words(seg.words):
            w_start = max(start, word.start - padding)
            w_end = min(end, word.end + padding)
            if w_end > w_start:
                spans.append((w_start, w_end))
    return sorted(spans)


def merge_spans(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Mescla intervalos sobrepostos/adjacentes — duas palavras ofensivas
    próximas uma da outra (com o padding somado) podem acabar se tocando."""
    if not spans:
        return []
    merged = [spans[0]]
    for s, e in spans[1:]:
        last_s, last_e = merged[-1]
        if s <= last_e:
            merged[-1] = (last_s, max(last_e, e))
        else:
            merged.append((s, e))
    return merged
