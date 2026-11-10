"""
Timeline de Revisão v1 — núcleo do PUT /clips/{id}/edit, do GET /draft e do
recut (docs/spec-timeline-v1.md §1, §4, §5 e §6).

Este é o ÚNICO lugar que converte tempos ABSOLUTOS (segundos do vídeo
original, o que o frontend usa) pros RELATIVOS ao início do clipe (o que o
banco guarda em caption_segments_override/manual_pan_keyframes/
manual_removed_ranges/cut_segments/mute_ranges). Antes cada router fazia a
sua conta de offset; com a regra de remapeamento (o que sobrevive, o que é
cortado na borda, o que vira perda) espalhada, o /edit e o recut iam
divergir. Por isso o recut também passa por aqui.

O arquivo tem duas metades:
1. Funções PURAS (sem banco, sem ffmpeg, sem disco): conversão, operações
   com intervalos, remapeamento, palavrões, validação e o `plan_edit`, que
   calcula tudo o que vai ser gravado. São as que os testes (Agente D)
   exercitam diretamente.
2. Orquestração (mais abaixo): monta o estado salvo a partir do Clip, chama
   o render e faz o compare-and-swap no banco.

Convenções:
- Intervalos são `(início, fim)` com fim > início. "Totalmente fora" de um
  corte = sobreposição de comprimento zero (encostar na borda não salva um
  intervalo: não sobraria nada pra mostrar/remover).
- Pontos (zoom) contam como dentro quando `start ≤ t ≤ end` (spec §4:
  "exatamente na borda conta como dentro").
- `_EPS` absorve erro de ponto flutuante nessas comparações — o frontend
  devolve tempos arredondados a 3 casas e o banco guarda o valor bruto.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Clip, Job, Video
from app.routers.upload import CAPTION_FONT_SIZE_MAX, CAPTION_FONT_SIZE_MIN
from app.schemas import (
    CaptionLineAbs,
    ClipCaptionStyle,
    ClipCaptionsDraft,
    ClipDraftOut,
    ClipEditIn,
    ClipProfanityDraft,
    EditChange,
    PanPointAbs,
    ProfanityRange,
    TimeRange,
    validate_caption_color,
    validate_caption_font_family,
    validate_caption_style,
)
# A metade pura (abaixo) só usa schemas/profanity/transcription; os imports
# de banco e render ficam aqui em cima por convenção, mas só são usados na
# orquestração — os testes das funções puras não tocam ffmpeg nem banco.
from app.services import clip_rendering, profanity, video_cutter
from app.services.caption_style import resolve_caption_style
from app.services.transcription import Segment, load_transcript_segments, segments_overlapping

logger = logging.getLogger(__name__)

# Tolerância de ponto flutuante nas comparações de borda (bem abaixo da
# tolerância de 1 ms da métrica #1 da spec).
_EPS = 1e-6

# Duração mínima de vídeo que precisa sobrar (contrato §1.2/§1.5: corte com
# menos de 0.5s é `invalid_range`; buracos que deixam menos que isso são
# `nothing_left`).
MIN_CLIP_DURATION = 0.5

# Sobras menores que isso são descartadas: (a) na derivação de `removed` de
# clipes antigos (contrato §2.4, "descarta sobras menores que 0,05s") e (b)
# nos trechos MANTIDOS derivados — um pedaço de 1-2 frames entre dois buracos
# só vira um "piscar" no vídeo e complica o concat do ffmpeg sem ganho.
MIN_SLIVER = 0.05

# Modos de Job.profanity_filter que censuram as falas do Whisper (spec §1.4).
CENSOR_MODES = {"cut", "censor"}


# ---------------------------------------------------------------------------
# Erro de domínio do /edit — vira o envelope {"detail": {code, message, ...}}
# no router (contrato §0.2). Fica aqui, e não como HTTPException, pra que as
# funções puras continuem testáveis sem FastAPI e o recut possa traduzir o
# mesmo erro pro formato ANTIGO ({"detail": "texto"}).
# ---------------------------------------------------------------------------


class EditError(Exception):
    def __init__(self, status_code: int, code: str, message: str, **extras: Any) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.extras = extras

    def to_detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.extras}


# ---------------------------------------------------------------------------
# 1. Conversão absoluto <-> relativo
# ---------------------------------------------------------------------------


def to_relative(t_abs: float, clip_start: float) -> float:
    """Tempo absoluto (vídeo original) -> relativo ao início do clipe."""
    return t_abs - clip_start


def to_absolute(t_rel: float, clip_start: float) -> float:
    """Tempo relativo ao início do clipe -> absoluto (vídeo original)."""
    return t_rel + clip_start


def ranges_to_relative(ranges: list[tuple[float, float]], clip_start: float) -> list[tuple[float, float]]:
    return [(to_relative(s, clip_start), to_relative(e, clip_start)) for s, e in ranges]


def ranges_to_absolute(ranges: list[tuple[float, float]], clip_start: float) -> list[tuple[float, float]]:
    return [(to_absolute(s, clip_start), to_absolute(e, clip_start)) for s, e in ranges]


def r3(x: float) -> float:
    """Arredondamento de TODA saída de tempo da API (spec §1.5, contrato §0.1)."""
    return round(x, 3)


# ---------------------------------------------------------------------------
# 2. Operações com listas de intervalos
# ---------------------------------------------------------------------------


def normalize_ranges(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Ordena e mescla intervalos sobrepostos ou encostados (contrato §1.2:
    "o backend ordena e mescla sobreposições"). Ignora intervalos vazios."""
    items = sorted((float(s), float(e)) for s, e in ranges if e - s > _EPS)
    merged: list[tuple[float, float]] = []
    for s, e in items:
        if merged and s <= merged[-1][1] + _EPS:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def complement_ranges(
    holes: list[tuple[float, float]], start: float, end: float, min_piece: float = 0.0,
) -> list[tuple[float, float]]:
    """Trechos de [start, end] NÃO cobertos por `holes` — usado pra virar
    "buracos" (removidos pelo usuário + cortes do filtro) em trechos
    MANTIDOS (o formato de cut_segments que o renderizador lê) e vice-versa.
    Pedaços menores que `min_piece` são descartados."""
    pieces: list[tuple[float, float]] = []
    cursor = start
    for s, e in normalize_ranges(holes):
        s, e = max(s, start), min(e, end)
        if e <= s:
            continue
        if s - cursor > max(min_piece, _EPS):
            pieces.append((cursor, s))
        cursor = max(cursor, e)
    if end - cursor > max(min_piece, _EPS):
        pieces.append((cursor, end))
    return pieces


def subtract_ranges(
    base: list[tuple[float, float]], minus: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """`base` menos `minus` (ambos listas de intervalos)."""
    result: list[tuple[float, float]] = []
    minus_n = normalize_ranges(minus)
    for s, e in normalize_ranges(base):
        result.extend(complement_ranges(minus_n, s, e))
    return result


def total_length(ranges: list[tuple[float, float]]) -> float:
    return sum(e - s for s, e in ranges)


def new_areas(old_start: float, old_end: float, new_start: float, new_end: float) -> list[tuple[float, float]]:
    """Partes do corte NOVO que não estavam no corte salvo — a "área nova"
    da spec §4, onde a legenda manual recebe as falas do Whisper. Funciona
    também quando os dois cortes nem se sobrepõem (aí a área nova é o corte
    novo inteiro)."""
    return complement_ranges([(old_start, old_end)], new_start, new_end)


def _overlap(a: float, b: float, start: float, end: float) -> float:
    return min(b, end) - max(a, start)


def _clip_change_text(a: float, b: float, start: float, end: float) -> str:
    """Texto do `change` (PT) de um intervalo cortado na borda."""
    cut_start = a < start - _EPS
    cut_end = b > end + _EPS
    if cut_start and cut_end:
        return f"início e fim cortados ({r3(start)}s–{r3(end)}s)"
    if cut_start:
        return f"início cortado em {r3(start)}s"
    return f"fim cortado em {r3(end)}s"


# ---------------------------------------------------------------------------
# 3. Remapeamento (spec §4)
# ---------------------------------------------------------------------------


def remap_caption_lines(
    lines: list[CaptionLineAbs], new_start: float, new_end: float,
) -> tuple[list[CaptionLineAbs], list[EditChange], list[EditChange]]:
    """Legenda MANUAL contra o corte novo. Devolve (mantidas, ajustes, perdas):
    - totalmente fora -> perda (precisa confirmar);
    - parcialmente fora -> cortada na borda (ajuste, só informa);
    - dentro -> mantida com o MESMO tempo absoluto.
    O texto nunca é alterado aqui (nem censurado: é do usuário)."""
    kept: list[CaptionLineAbs] = []
    adjustments: list[EditChange] = []
    losses: list[EditChange] = []
    for line in sorted(lines, key=lambda ln: (ln.start, ln.end)):
        a, b = line.start, line.end
        if _overlap(a, b, new_start, new_end) <= _EPS:
            losses.append(EditChange(kind="caption", start=r3(a), end=r3(b), text=line.text))
            continue
        if a < new_start - _EPS or b > new_end + _EPS:
            ns, ne = max(a, new_start), min(b, new_end)
            kept.append(CaptionLineAbs(start=ns, end=ne, text=line.text))
            adjustments.append(EditChange(
                kind="caption", start=r3(ns), end=r3(ne), text=line.text,
                change=_clip_change_text(a, b, new_start, new_end),
            ))
        else:
            kept.append(CaptionLineAbs(start=a, end=b, text=line.text))
    return kept, adjustments, losses


def fill_new_areas_with_whisper(
    kept_lines: list[CaptionLineAbs],
    whisper_segments: list[Segment],
    areas: list[tuple[float, float]],
    censor: bool,
) -> tuple[list[CaptionLineAbs], list[EditChange]]:
    """Na área nova de um corte que cresceu, a legenda manual recebe as falas
    do Whisper daquela área (spec §4), censuradas se o filtro estiver ligado.

    - Critério de quais falas entram: o mesmo `segments_overlapping` (≥ 50%)
      do resto do sistema, mas medido contra a ÁREA NOVA — uma fala que só
      encosta na área nova não entra.
    - A fala é cortada nos limites da área nova, pra não sobrepor a legenda
      manual que já existe no trecho antigo (senão o .srt mostraria as duas
      empilhadas, uma delas repetindo o texto da outra).
    - Uma fala que ainda assim sobreporia uma linha manual mantida é pulada:
      o texto do usuário tem prioridade.
    Devolve (linhas_finais_ordenadas, ajustes) — cada fala adicionada vira um
    ajuste ("criado pelo backend", contrato §1.3)."""
    result = list(kept_lines)
    adjustments: list[EditChange] = []
    for area_start, area_end in areas:
        for seg in segments_overlapping(whisper_segments, area_start, area_end):
            text = (profanity.redact_text(seg.text) if censor else seg.text).strip()
            if not text:
                continue
            ns, ne = max(seg.start, area_start), min(seg.end, area_end)
            if ne - ns <= _EPS:
                continue
            if any(_overlap(ns, ne, ln.start, ln.end) > _EPS for ln in kept_lines):
                continue
            result.append(CaptionLineAbs(start=ns, end=ne, text=text))
            adjustments.append(EditChange(
                kind="caption", start=r3(ns), end=r3(ne), text=text,
                change="fala da transcrição adicionada na área nova do corte",
            ))
    result.sort(key=lambda ln: (ln.start, ln.end))
    return result, adjustments


def _interpolate_cx(p0: PanPointAbs, p1: PanPointAbs, t: float) -> float:
    if p1.t == p0.t:
        return p1.cx
    ratio = (t - p0.t) / (p1.t - p0.t)
    return p0.cx + (p1.cx - p0.cx) * ratio


def remap_pan_points(
    points: list[PanPointAbs], new_start: float, new_end: float,
) -> tuple[list[PanPointAbs], list[EditChange], list[EditChange]]:
    """Pontos de zoom contra o corte novo (spec §4). Devolve (mantidos,
    ajustes, perdas):
    - ponto com `start ≤ t ≤ end` fica, com o mesmo tempo absoluto;
    - ponto fora é perda;
    - se há um ponto DENTRO e o vizinho imediato FORA, cria um ponto
      interpolado exatamente na borda (ajuste), pro enquadramento na borda
      não mudar. Se já existe ponto exatamente na borda, nada a criar.
    Sem nenhum ponto dentro, todos viram perda e não há interpolação (a
    regra da spec exige "um ponto dentro e o vizinho fora")."""
    pts = sorted(points, key=lambda p: p.t)
    inside = [p for p in pts if new_start - _EPS <= p.t <= new_end + _EPS]
    outside = [p for p in pts if not (new_start - _EPS <= p.t <= new_end + _EPS)]
    # Clamp de quem está "na borda" por erro de arredondamento, pra o ponto
    # salvo nunca cair fora de [0, duração] no relativo.
    kept = [PanPointAbs(t=min(max(p.t, new_start), new_end), cx=p.cx) for p in inside]
    losses = [EditChange(kind="pan", start=r3(p.t), end=r3(p.t)) for p in outside]
    adjustments: list[EditChange] = []
    if kept:
        left_out = [p for p in outside if p.t < new_start]
        if left_out and kept[0].t > new_start + _EPS:
            cx = _interpolate_cx(left_out[-1], kept[0], new_start)
            kept.insert(0, PanPointAbs(t=new_start, cx=cx))
            adjustments.append(EditChange(
                kind="pan", start=r3(new_start), end=r3(new_start),
                change=f"ponto interpolado criado na borda ({r3(new_start)}s)",
            ))
        right_out = [p for p in outside if p.t > new_end]
        if right_out and kept[-1].t < new_end - _EPS:
            cx = _interpolate_cx(kept[-1], right_out[0], new_end)
            kept.append(PanPointAbs(t=new_end, cx=cx))
            adjustments.append(EditChange(
                kind="pan", start=r3(new_end), end=r3(new_end),
                change=f"ponto interpolado criado na borda ({r3(new_end)}s)",
            ))
    return kept, adjustments, losses


def remap_removed_ranges(
    ranges: list[tuple[float, float]], new_start: float, new_end: float,
) -> tuple[list[tuple[float, float]], list[EditChange], list[EditChange]]:
    """Trechos removidos PELO USUÁRIO contra o corte novo — mesma regra da
    legenda manual (perda / cortado na borda / mantido). Normaliza (ordena e
    mescla) antes, então perdas e ajustes se referem aos trechos já mesclados."""
    kept: list[tuple[float, float]] = []
    adjustments: list[EditChange] = []
    losses: list[EditChange] = []
    for a, b in normalize_ranges(ranges):
        if _overlap(a, b, new_start, new_end) <= _EPS:
            losses.append(EditChange(kind="removed", start=r3(a), end=r3(b)))
            continue
        if a < new_start - _EPS or b > new_end + _EPS:
            ns, ne = max(a, new_start), min(b, new_end)
            kept.append((ns, ne))
            adjustments.append(EditChange(
                kind="removed", start=r3(ns), end=r3(ne),
                change=_clip_change_text(a, b, new_start, new_end),
            ))
        else:
            kept.append((a, b))
    return kept, adjustments, losses


# ---------------------------------------------------------------------------
# 4. Palavrões e legenda automática
# ---------------------------------------------------------------------------


def compute_profanity_ranges(
    segments: list[Segment], start: float, end: float, mode: str,
) -> list[tuple[float, float]]:
    """Trechos ABSOLUTOS das palavras ofensivas em [start, end], recalculados
    do zero (nunca herdados do corte antigo — spec §4) com o mesmo cálculo do
    pipeline (padding + merge). `[]` com o filtro desligado."""
    if mode not in CENSOR_MODES or not segments:
        return []
    return profanity.merge_spans(profanity.find_profanity_spans_in_range(segments, start, end))


def auto_caption_lines(
    segments: list[Segment], start: float, end: float, censor: bool,
) -> list[CaptionLineAbs]:
    """Legenda em modo `auto`: falas do Whisper no corte (`segments_overlapping`,
    ≥ 50%), censuradas quando o filtro estiver ligado. Tempos do próprio
    Whisper (sem cortar na borda, como o resto do sistema sempre fez — o
    render já faz o clamp em 0)."""
    overlapping = segments_overlapping(segments, start, end) if segments else []
    if censor:
        overlapping = profanity.redact_segments(overlapping)
    return [
        CaptionLineAbs(start=seg.start, end=seg.end, text=seg.text)
        for seg in overlapping
        if seg.text.strip()
    ]


def derive_removed_from_cut_segments(
    cut_segments_rel: list[tuple[float, float]] | None,
    clip_start: float,
    clip_end: float,
    profanity_cut_abs: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """`removed` (ABSOLUTO) de um clipe antigo, que tem cut_segments mas
    manual_removed_ranges NULL (contrato §2.4): buracos de cut_segments,
    menos os cortes do filtro (modo "cut"), descartando sobras < 0,05s."""
    if not cut_segments_rel:
        return []
    duration = clip_end - clip_start
    holes_rel = complement_ranges(cut_segments_rel, 0.0, duration)
    holes_abs = ranges_to_absolute(holes_rel, clip_start)
    leftovers = subtract_ranges(holes_abs, profanity_cut_abs)
    return [(s, e) for s, e in leftovers if e - s >= MIN_SLIVER]


def build_keep_segments(
    removed_abs: list[tuple[float, float]],
    profanity_cut_abs: list[tuple[float, float]],
    start: float,
    end: float,
) -> list[tuple[float, float]] | None:
    """cut_segments (trechos MANTIDOS, RELATIVOS) = complemento de
    (removidos do usuário ∪ cortes do filtro) dentro do corte (spec §2.1).
    None quando não há buraco nenhum — a mesma representação de "sem cortes
    internos" que o resto do sistema usa."""
    holes = normalize_ranges(list(removed_abs) + list(profanity_cut_abs))
    if not holes:
        return None
    keep_abs = complement_ranges(holes, start, end, min_piece=MIN_SLIVER)
    if len(keep_abs) == 1 and keep_abs[0][0] <= start + _EPS and keep_abs[0][1] >= end - _EPS:
        return None
    return ranges_to_relative(keep_abs, start)


def kept_duration(keep_segments_rel: list[tuple[float, float]] | None, start: float, end: float) -> float:
    return (end - start) if keep_segments_rel is None else total_length(keep_segments_rel)


# ---------------------------------------------------------------------------
# 5. Validação de VALOR do payload do /edit (contrato §1.2/§1.5)
#
# Fica aqui, e não no Pydantic, porque o contrato pede 400 com `code`
# específico; um validator do Pydantic viraria 422 (handoff do Agente B).
# ---------------------------------------------------------------------------


def _finite(*values: float) -> bool:
    return all(math.isfinite(v) for v in values)


def validate_caption_style_values(style: ClipCaptionStyle) -> None:
    """400 invalid_style com `field` = nome do campo em ClipCaptionStyle."""
    if not (CAPTION_FONT_SIZE_MIN <= style.font_size <= CAPTION_FONT_SIZE_MAX):
        raise EditError(
            400, "invalid_style",
            f"Tamanho de fonte inválido ({style.font_size}). Use um valor entre "
            f"{CAPTION_FONT_SIZE_MIN} e {CAPTION_FONT_SIZE_MAX}.",
            field="font_size",
        )
    for field_name, value, validator in (
        ("color", style.color, validate_caption_color),
        ("preset", style.preset, validate_caption_style),
        ("font_family", style.font_family, validate_caption_font_family),
    ):
        try:
            validator(value)
        except ValueError as exc:
            raise EditError(400, "invalid_style", str(exc), field=field_name) from exc


def validate_edit_payload(
    payload: ClipEditIn, video_duration: float | None, has_streamer_region: bool,
) -> None:
    """Todas as checagens 400 do /edit. `video_duration`/`has_streamer_region`
    vêm do clipe; quando o clipe não existe o router passa None/False e as
    checagens que dependem dele simplesmente não rodam (o 404 vem logo
    depois, respeitando a ordem "validação -> not_found")."""
    start, end = payload.range.start, payload.range.end
    if not _finite(start, end) or end <= start:
        raise EditError(400, "invalid_range", "O fim do corte precisa ser maior que o início.")
    if start < 0 or (video_duration is not None and end > video_duration + _EPS):
        limit = f" (0 – {r3(video_duration)}s)" if video_duration is not None else ""
        raise EditError(400, "invalid_range", f"O corte precisa ficar dentro da duração do vídeo{limit}.")
    if end - start < MIN_CLIP_DURATION:
        raise EditError(400, "invalid_range", f"O corte precisa ter pelo menos {MIN_CLIP_DURATION}s.")

    # Trecho removido com fim ≤ início: o contrato (§1.2) exige end > start
    # mas não lista um código próprio — usa invalid_range, que é o código de
    # "intervalo inválido".
    for i, rng in enumerate(payload.removed):
        if not _finite(rng.start, rng.end) or rng.end <= rng.start:
            raise EditError(
                400, "invalid_range",
                f"O trecho removido {i + 1} precisa terminar depois de começar.",
            )

    if payload.captions.mode == "manual":
        if not payload.captions.lines:
            # Lista vazia em modo manual não tem representação no banco
            # (override vazio == NULL == modo auto). Erro explícito em vez de
            # trocar o modo em silêncio.
            raise EditError(
                400, "invalid_caption",
                "A legenda manual precisa de pelo menos uma linha (ou use o modo automático).",
            )
        for i, line in enumerate(payload.captions.lines):
            if not line.text.strip():
                raise EditError(400, "invalid_caption", f"A linha {i + 1} da legenda está sem texto.", index=i)
            if not _finite(line.start, line.end) or line.end <= line.start:
                raise EditError(
                    400, "invalid_caption",
                    f"A linha {i + 1} da legenda precisa terminar depois de começar.", index=i,
                )

    if payload.pan and has_streamer_region:
        raise EditError(
            400, "invalid_pan",
            "Este vídeo usa tela dividida (streamer detectado); o zoom manual não é suportado nele.",
        )
    seen_t: set[float] = set()
    for i, point in enumerate(payload.pan):
        if not _finite(point.t, point.cx) or not (0.0 <= point.cx <= 1.0):
            raise EditError(
                400, "invalid_pan",
                f"O ponto de zoom {i + 1} tem posição inválida ({point.cx}); use um valor entre 0 e 1.",
                index=i,
            )
        if point.t in seen_t:
            # Dois pontos no mesmo t dividiriam por zero na expressão de
            # pan do ffmpeg (ver pan.py::_validate_keyframes).
            raise EditError(
                400, "invalid_pan", f"O ponto de zoom {i + 1} repete o tempo {r3(point.t)}s.", index=i,
            )
        seen_t.add(point.t)

    validate_caption_style_values(payload.caption_style)


# ---------------------------------------------------------------------------
# 6. Plano do /edit (puro): o que vai ser renderizado e gravado
# ---------------------------------------------------------------------------


@dataclass
class SavedClipState:
    """Estado SALVO do clipe, já em tempos ABSOLUTOS — o mesmo formato do
    rascunho. Base do GET /draft e ponto de partida do recut."""

    start: float
    end: float
    captions_mode: str  # "auto" | "manual"
    caption_lines: list[CaptionLineAbs]  # manual: override; auto: Whisper (censurado se o filtro estiver ligado)
    pan: list[PanPointAbs]
    removed: list[tuple[float, float]]
    profanity_mode: str
    profanity_ranges: list[tuple[float, float]]


@dataclass
class EditPlan:
    """Resultado do remapeamento + recálculo, ainda sem render e sem banco."""

    start: float
    end: float
    captions_mode: str
    caption_lines: list[CaptionLineAbs]  # ABSOLUTAS: o que a legenda vai mostrar (antes dos cortes internos)
    pan: list[PanPointAbs]
    removed: list[tuple[float, float]]
    profanity_mode: str
    profanity_ranges: list[tuple[float, float]]
    adjustments: list[EditChange] = field(default_factory=list)
    losses: list[EditChange] = field(default_factory=list)
    # O rascunho tinha zoom manual, mas nenhum ponto chega ao render: o vídeo
    # sai com o enquadramento automático. Só informa; não bloqueia sozinho.
    framing_fallback: bool = False

    # Valores RELATIVOS, prontos pro banco (contrato §1.6, tabela "O que é
    # gravado no sucesso"), em coordenadas ANTES dos cortes internos.
    caption_override_rel: list[dict] | None = None
    pan_rel: list[tuple[float, float]] | None = None
    removed_rel: list[tuple[float, float]] | None = None
    cut_segments_rel: list[tuple[float, float]] | None = None
    mute_ranges_rel: list[tuple[float, float]] | None = None


def build_saved_state(
    *,
    clip_start: float,
    clip_end: float,
    caption_override_rel: list[dict] | None,
    pan_rel: list[tuple[float, float]] | None,
    removed_rel: list[tuple[float, float]] | None,
    cut_segments_rel: list[tuple[float, float]] | None,
    segments: list[Segment],
    profanity_mode: str,
) -> SavedClipState:
    """Converte as colunas RELATIVAS do Clip pro estado ABSOLUTO (GET /draft
    §2.2). Recebe os valores já desserializados (não o Clip) pra ser pura."""
    censor = profanity_mode in CENSOR_MODES
    prof = compute_profanity_ranges(segments, clip_start, clip_end, profanity_mode)

    if caption_override_rel is not None:
        mode = "manual"
        # Texto do usuário: SEM censura (spec §1.4).
        lines = [
            CaptionLineAbs(start=to_absolute(s["start"], clip_start), end=to_absolute(s["end"], clip_start), text=s["text"])
            for s in caption_override_rel
        ]
        lines.sort(key=lambda ln: (ln.start, ln.end))
    else:
        mode = "auto"
        lines = auto_caption_lines(segments, clip_start, clip_end, censor)

    pan = sorted(
        (PanPointAbs(t=to_absolute(t, clip_start), cx=cx) for t, cx in (pan_rel or [])),
        key=lambda p: p.t,
    )

    if removed_rel is not None:
        removed = normalize_ranges(ranges_to_absolute(removed_rel, clip_start))
    else:
        # Clipe antigo (PUT /cuts legado ou pipeline): deriva dos buracos de
        # cut_segments, tirando o que é do filtro (contrato §2.4).
        prof_cut = prof if profanity_mode == "cut" else []
        removed = derive_removed_from_cut_segments(cut_segments_rel, clip_start, clip_end, prof_cut)

    return SavedClipState(
        start=clip_start, end=clip_end, captions_mode=mode, caption_lines=lines,
        pan=pan, removed=removed, profanity_mode=profanity_mode, profanity_ranges=prof,
    )


def plan_edit(
    *,
    saved_start: float,
    saved_end: float,
    new_start: float,
    new_end: float,
    captions_mode: str,
    caption_lines: list[CaptionLineAbs],
    pan: list[PanPointAbs],
    removed: list[tuple[float, float]],
    segments: list[Segment],
    profanity_mode: str,
) -> EditPlan:
    """Remapeia o rascunho (ABSOLUTO) contra o corte novo, recalcula os
    palavrões, aplica a censura nas falas do Whisper e calcula os valores
    RELATIVOS que vão pro banco. Puro: quem decide o que fazer com
    `plan.losses` (409 no /edit, descartar no recut) é o chamador.

    Levanta EditError(400, "nothing_left") se os buracos (usuário + filtro)
    deixarem menos de 0.5s de vídeo."""
    censor = profanity_mode in CENSOR_MODES
    adjustments: list[EditChange] = []
    losses: list[EditChange] = []

    # Legenda
    if captions_mode == "manual":
        kept_lines, adj, lost = remap_caption_lines(caption_lines, new_start, new_end)
        adjustments += adj
        losses += lost
        kept_lines, adj = fill_new_areas_with_whisper(
            kept_lines, segments, new_areas(saved_start, saved_end, new_start, new_end), censor,
        )
        adjustments += adj
        final_lines = kept_lines
    else:
        # Modo auto: sem perda nem ajuste, sempre recalculada (spec §4).
        final_lines = auto_caption_lines(segments, new_start, new_end, censor)

    # Zoom
    kept_pan, adj, lost = remap_pan_points(pan, new_start, new_end)
    adjustments += adj
    losses += lost

    # Removidos pelo usuário
    kept_removed, adj, lost = remap_removed_ranges(removed, new_start, new_end)
    adjustments += adj
    losses += lost

    # Palavrões: do zero pra janela nova; nunca entram como perda.
    prof = compute_profanity_ranges(segments, new_start, new_end, profanity_mode)
    prof_cut = prof if profanity_mode == "cut" else []

    keep_rel = build_keep_segments(kept_removed, prof_cut, new_start, new_end)
    if kept_duration(keep_rel, new_start, new_end) < MIN_CLIP_DURATION:
        raise EditError(
            400, "nothing_left",
            f"Os trechos removidos deixam menos de {MIN_CLIP_DURATION}s de vídeo.",
        )

    plan = EditPlan(
        start=new_start, end=new_end, captions_mode=captions_mode, caption_lines=final_lines,
        pan=kept_pan, removed=kept_removed, profanity_mode=profanity_mode, profanity_ranges=prof,
        adjustments=adjustments, losses=losses,
    )
    if captions_mode == "manual":
        plan.caption_override_rel = [
            {"start": to_relative(ln.start, new_start), "end": to_relative(ln.end, new_start), "text": ln.text.strip()}
            for ln in final_lines
        ] or None
    plan.pan_rel = [(to_relative(p.t, new_start), p.cx) for p in kept_pan] or None
    plan.removed_rel = ranges_to_relative(kept_removed, new_start) or None
    plan.cut_segments_rel = keep_rel
    # Mesma função que monta o rastro do render, pra o aviso não divergir dele.
    plan.framing_fallback = bool(pan) and clip_rendering.remap_pan_track(plan.pan_rel, keep_rel) is None
    # Mudos só existem no modo "censor"; no "cut" o palavrão vira buraco em
    # cut_segments (acima). Guardados PRÉ-corte, como Clip.mute_ranges sempre foi.
    if profanity_mode == "censor":
        plan.mute_ranges_rel = ranges_to_relative(prof, new_start) or None
    return plan


def losses_message(count: int) -> str:
    if count == 1:
        return "1 item ficou fora do novo corte e será descartado."
    return f"{count} itens ficaram fora do novo corte e serão descartados."


def change_to_json(change: EditChange) -> dict[str, Any]:
    """EditChange sem os campos que não se aplicam (`text` fora de caption,
    `change` em losses) — contrato §1.3. O exclude_none fica SÓ aqui, pra não
    sumir com campos que o contrato exige como null em outros objetos."""
    return change.model_dump(exclude_none=True)


def make_draft_out(
    *,
    clip_id: str,
    job_id: str,
    revision: int,
    video_duration: float | None,
    has_streamer_region: bool,
    start: float,
    end: float,
    caption_style: ClipCaptionStyle,
    captions_mode: str,
    caption_lines: list[CaptionLineAbs],
    pan: list[PanPointAbs],
    removed: list[tuple[float, float]],
    profanity_mode: str,
    profanity_ranges: list[tuple[float, float]],
) -> ClipDraftOut:
    """Monta o ClipDraftOut (contrato §2.2) com todos os tempos em 3 casas e
    as listas em ordem crescente. Usado pelo GET /draft e pela resposta do
    /edit (que devolve o rascunho já remapeado)."""
    kind = "cut" if profanity_mode == "cut" else "muted"
    return ClipDraftOut(
        clip_id=clip_id,
        job_id=job_id,
        revision=revision,
        video_duration=r3(video_duration) if video_duration is not None else None,
        has_streamer_region=has_streamer_region,
        range=TimeRange(start=r3(start), end=r3(end)),
        caption_style=caption_style,
        captions=ClipCaptionsDraft(
            mode=captions_mode,
            lines=[
                CaptionLineAbs(start=r3(ln.start), end=r3(ln.end), text=ln.text)
                for ln in sorted(caption_lines, key=lambda ln: (ln.start, ln.end))
            ],
        ),
        pan=[PanPointAbs(t=r3(p.t), cx=p.cx) for p in sorted(pan, key=lambda p: p.t)],
        removed=[TimeRange(start=r3(s), end=r3(e)) for s, e in sorted(removed)],
        profanity=ClipProfanityDraft(
            mode=profanity_mode if profanity_mode in ("off", "cut", "censor") else "off",
            ranges=[ProfanityRange(start=r3(s), end=r3(e), kind=kind) for s, e in sorted(profanity_ranges)]
            if profanity_mode in CENSOR_MODES else [],
        ),
    )


# ===========================================================================
# Orquestração (banco + render). Daqui pra baixo NÃO é puro.
# ===========================================================================

# campo do ClipCaptionStyle -> coluna do Clip (contrato §0.3).
STYLE_COLUMNS: dict[str, str] = {
    "enabled": "caption_style_enabled",
    "font_size": "caption_style_font_size",
    "color": "caption_style_color",
    "preset": "caption_style_preset",
    "font_family": "caption_style_font_family",
}


def load_segments(job_id: str) -> list[Segment]:
    """Falas do Whisper (texto BRUTO) salvas no pipeline; [] se não houver."""
    return load_transcript_segments(settings.data_dir / job_id)


def saved_state_from_clip(clip: Clip, job: Job | None, segments: list[Segment]) -> SavedClipState:
    return build_saved_state(
        clip_start=clip.start_time,
        clip_end=clip.end_time,
        caption_override_rel=clip.get_caption_segments_override(),
        pan_rel=clip.get_manual_pan_keyframes(),
        removed_rel=clip.get_manual_removed_ranges(),
        cut_segments_rel=clip.get_cut_segments(),
        segments=segments,
        profanity_mode=(job.profanity_filter if job else None) or "off",
    )


def draft_from_clip(clip: Clip, job: Job | None, video: Video | None) -> ClipDraftOut:
    """GET /clips/{id}/draft (contrato §2): só leitura, sem lock."""
    state = saved_state_from_clip(clip, job, load_segments(clip.job_id))
    return make_draft_out(
        clip_id=clip.id,
        job_id=clip.job_id,
        revision=clip.revision or 0,
        video_duration=video.duration if video else None,
        has_streamer_region=bool(job and job.get_streamer_region()),
        start=state.start,
        end=state.end,
        caption_style=resolve_caption_style(clip, job),
        captions_mode=state.captions_mode,
        caption_lines=state.caption_lines,
        pan=state.pan,
        removed=state.removed,
        profanity_mode=state.profanity_mode,
        profanity_ranges=state.profanity_ranges,
    )


def render_plan(
    clip: Clip, video: Video, job: Job | None, plan: EditPlan, style: ClipCaptionStyle,
) -> tuple[Path, str]:
    """Renderiza o plano num arquivo novo (`_r{n+1}.mp4`). Devolve (caminho,
    texto_que_sobrou) — o texto alimenta transcript_text/hashtags/títulos.

    A legenda vai pro .srt já remapeada pelos cortes internos; o pan e os
    mudos também, só PRA ESSA renderização (o banco guarda tudo PRÉ-corte —
    ver docstring de clip_rendering.remap_pan_track)."""
    keep = plan.cut_segments_rel
    abs_segments = [Segment(start=ln.start, end=ln.end, text=ln.text) for ln in plan.caption_lines]
    srt_segments, text = clip_rendering.remap_clip_segments(abs_segments, plan.start, keep)
    new_path = clip_rendering.render_clip(
        clip,
        video,
        job,
        start_time=plan.start,
        end_time=plan.end,
        captions_enabled=style.enabled,
        caption_font_size=style.font_size,
        caption_color=style.color,
        caption_style=style.preset,
        caption_font_family=style.font_family,
        srt_segments=srt_segments,
        manual_pan_track=clip_rendering.remap_pan_track(plan.pan_rel, keep),
        cut_segments=keep,
        mute_ranges=clip_rendering.remap_mute_ranges(plan.mute_ranges_rel, keep),
    )
    return new_path, text


def save_plan_cas(
    db: Session,
    clip: Clip,
    plan: EditPlan,
    new_path: Path,
    text: str,
    style_columns: dict[str, Any],
    base_revision: int,
) -> bool:
    """Compare-and-swap (spec §5 passo 10):
    `UPDATE clips SET ..., processed_path = novo, revision = revision + 1
     WHERE id = ? AND revision = ?`.
    - 1 linha: commit, apaga o arquivo antigo (best-effort), devolve True.
    - 0 linhas: alguém gravou antes; apaga o arquivo novo, devolve False.
    - exceção: rollback, apaga o arquivo novo e relança.
    O banco é o único ponto de virada: até o commit, processed_path aponta
    pro arquivo antigo, que continua existindo.

    `style_columns`: colunas caption_style_* a gravar — as 5 no /edit, só
    as enviadas no recut (contrato §4.5)."""
    # Reaproveita os setters do modelo (Agente B) num Clip transitório (fora
    # da sessão) só pra serializar igualzinho ao resto do sistema — lista
    # vazia vira NULL, mesmo formato JSON.
    scratch = Clip()
    scratch.set_caption_segments_override(plan.caption_override_rel)
    scratch.set_manual_pan_keyframes(plan.pan_rel)
    scratch.set_manual_removed_ranges(plan.removed_rel)
    scratch.set_cut_segments(plan.cut_segments_rel)
    scratch.set_mute_ranges(plan.mute_ranges_rel)
    hashtags_json, titles_json = clip_rendering.compute_clip_metadata(text)

    values: dict[str, Any] = {
        "start_time": plan.start,
        "end_time": plan.end,
        "caption_segments_override": scratch.caption_segments_override,
        "manual_pan_keyframes": scratch.manual_pan_keyframes,
        "manual_removed_ranges": scratch.manual_removed_ranges,
        "cut_segments": scratch.cut_segments,
        "mute_ranges": scratch.mute_ranges,
        "transcript_text": text,
        "hashtags": hashtags_json,
        "suggested_titles": titles_json,
        "processed_path": str(new_path),
        "revision": Clip.revision + 1,
        **style_columns,
    }
    old_path = clip.processed_path
    try:
        result = db.execute(
            update(Clip)
            .where(Clip.id == clip.id, Clip.revision == base_revision)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            db.rollback()
            new_path.unlink(missing_ok=True)
            return False
        db.commit()
    except Exception:
        db.rollback()
        new_path.unlink(missing_ok=True)
        raise
    # O objeto `clip` da sessão não viu o UPDATE (synchronize_session=False):
    # recarrega antes de qualquer um ler dele.
    db.refresh(clip)
    clip_rendering.discard_old_render(old_path, new_path)
    return True


def _current_revision(db: Session, clip_id: str) -> int:
    value = db.query(Clip.revision).filter(Clip.id == clip_id).scalar()
    return value or 0


def _revision_conflict(current: int) -> EditError:
    return EditError(
        409, "revision_conflict",
        "Este corte foi alterado em outra aba. Recarregue para ver a versão atual.",
        current_revision=current,
    )


def _ffmpeg_tail(exc: Exception, lines: int = 15) -> str | None:
    if not isinstance(exc, video_cutter.FFmpegError):
        return None
    return "\n".join(str(exc).splitlines()[-lines:])


@dataclass
class EditOutcome:
    """Resultado do /edit pro router montar o ClipEditOut."""

    status: str  # "rendered" | "dry_run"
    clip: Clip
    draft: ClipDraftOut
    adjustments: list[EditChange]
    render_seconds: float | None


def _plan_from_payload(clip: Clip, job: Job | None, payload: ClipEditIn) -> EditPlan:
    """Passos 3 a 7 do /edit (revision -> remapear -> nothing_left -> perdas
    -> palavrões/censura). Comum ao dry_run e ao PUT real."""
    if payload.revision != (clip.revision or 0):
        raise _revision_conflict(clip.revision or 0)
    plan = plan_edit(
        saved_start=clip.start_time,
        saved_end=clip.end_time,
        new_start=payload.range.start,
        new_end=payload.range.end,
        captions_mode=payload.captions.mode,
        caption_lines=payload.captions.lines,
        pan=payload.pan,
        removed=[(r.start, r.end) for r in payload.removed],
        segments=load_segments(clip.job_id),
        profanity_mode=(job.profanity_filter if job else None) or "off",
    )
    if plan.losses and not payload.confirm_losses:
        raise EditError(
            409, "losses_require_confirmation", losses_message(len(plan.losses)),
            losses=[change_to_json(c) for c in plan.losses],
            framing_fallback=plan.framing_fallback,
        )
    return plan


def _draft_from_plan(
    clip: Clip, job: Job | None, video: Video, plan: EditPlan, style: ClipCaptionStyle, revision: int,
) -> ClipDraftOut:
    return make_draft_out(
        clip_id=clip.id,
        job_id=clip.job_id,
        revision=revision,
        video_duration=video.duration,
        has_streamer_region=bool(job and job.get_streamer_region()),
        start=plan.start,
        end=plan.end,
        caption_style=style,
        captions_mode=plan.captions_mode,
        caption_lines=plan.caption_lines,
        pan=plan.pan,
        removed=plan.removed,
        profanity_mode=plan.profanity_mode,
        profanity_ranges=plan.profanity_ranges,
    )


def run_edit(db: Session, clip_id: str, payload: ClipEditIn, dry_run: bool) -> EditOutcome:
    """PUT /clips/{id}/edit, na ordem do contrato §1.5 / spec §5:
    validação (400) -> not_found -> clip_not_rendered -> lock ->
    revision_conflict -> remapeamento -> nothing_left ->
    losses_require_confirmation -> palavrões/censura -> (dry_run para aqui)
    -> render -> compare-and-swap -> apaga o antigo -> libera o lock."""
    clip = db.get(Clip, clip_id)
    job = db.get(Job, clip.job_id) if clip is not None else None
    video = db.get(Video, clip.video_id) if clip is not None else None

    # Validação ANTES do 404 (ordem do contrato). As checagens que dependem
    # do clipe (duração do vídeo, tela dividida) usam o que existir; sem
    # clipe, ficam de fora e o 404 vem logo abaixo.
    validate_edit_payload(
        payload,
        video.duration if video is not None else None,
        bool(job and job.get_streamer_region()),
    )
    if clip is None:
        raise EditError(404, "not_found", "Corte não encontrado.")
    if video is None or not video.filepath or not Path(video.filepath).exists():
        raise EditError(404, "not_found", "O vídeo original deste corte não foi encontrado.")
    if not clip.processed_path:
        raise EditError(409, "clip_not_rendered", "Este corte ainda não tem um vídeo gerado.")

    style = payload.caption_style

    if dry_run:
        # Sem lock (não renderiza, não disputa arquivo), sem gravar, sem
        # incrementar revision. O `draft.revision` continua o ATUAL: é ele
        # que a tela precisaria mandar num /edit de verdade.
        plan = _plan_from_payload(clip, job, payload)
        return EditOutcome(
            status="dry_run", clip=clip,
            draft=_draft_from_plan(clip, job, video, plan, style, clip.revision or 0),
            adjustments=plan.adjustments, render_seconds=None,
        )

    with clip_rendering.clip_render_lock(clip.id):
        # Outra renderização pode ter terminado entre o db.get lá em cima e
        # o lock: relê pra conferir a revision e calcular o nome do arquivo
        # novo em cima do estado atual.
        db.refresh(clip)
        plan = _plan_from_payload(clip, job, payload)
        base_revision = clip.revision or 0

        started = time.monotonic()
        try:
            new_path, text = render_plan(clip, video, job, plan, style)
        except Exception as exc:  # noqa: BLE001 — qualquer falha do ffmpeg/disco vira 500 render_failed
            logger.exception("Render do /edit falhou (clip %s).", clip.id)
            extras = {"ffmpeg_tail": tail} if (tail := _ffmpeg_tail(exc)) else {}
            raise EditError(
                500, "render_failed",
                "A renderização falhou. Nada foi alterado; o corte anterior continua valendo.",
                **extras,
            ) from exc
        render_seconds = time.monotonic() - started

        style_columns = {STYLE_COLUMNS[name]: getattr(style, name) for name in STYLE_COLUMNS}
        try:
            swapped = save_plan_cas(db, clip, plan, new_path, text, style_columns, base_revision)
        except Exception as exc:  # noqa: BLE001 — commit falhou: banco e arquivo antigos intactos
            logger.exception("Commit do /edit falhou (clip %s).", clip.id)
            raise EditError(
                500, "render_failed",
                "A renderização falhou. Nada foi alterado; o corte anterior continua valendo.",
            ) from exc
        if not swapped:
            raise _revision_conflict(_current_revision(db, clip.id))

        return EditOutcome(
            status="rendered", clip=clip,
            draft=_draft_from_plan(clip, job, video, plan, style, clip.revision or 0),
            adjustments=plan.adjustments, render_seconds=r3(render_seconds),
        )


def run_recut(
    db: Session,
    clip: Clip,
    video: Video,
    job: Job | None,
    new_start: float,
    new_end: float,
    style_overrides: dict[str, Any],
) -> bool:
    """Miolo do POST /clips/{id}/recut (spec §6, contrato §4.5), usando o
    MESMO remapeamento do /edit em vez de apagar as edições:
    - parte do estado salvo (legenda manual, zoom, removidos — num clipe
      antigo, `removed` vem da derivação da §2.4);
    - age como `confirm_losses: true` (a tela antiga não tem modal): o que
      fica TOTALMENTE fora do corte novo é descartado;
    - palavrões recalculados, falas do Whisper censuradas;
    - grava só as colunas caption_style_* dos campos ENVIADOS
      (`style_overrides`: campo do ClipCaptionStyle -> valor).
    Precisa rodar dentro do clip_render_lock, com `clip` recém-relido.
    Devolve False em conflito de revision (só acontece com mais de um
    processo); EditError(nothing_left) e falhas de render sobem."""
    segments = load_segments(clip.job_id)
    profanity_mode = (job.profanity_filter if job else None) or "off"
    state = saved_state_from_clip(clip, job, segments)
    plan = plan_edit(
        saved_start=state.start,
        saved_end=state.end,
        new_start=new_start,
        new_end=new_end,
        captions_mode=state.captions_mode,
        caption_lines=state.caption_lines,
        pan=state.pan,
        removed=state.removed,
        segments=segments,
        profanity_mode=profanity_mode,
    )
    # Campos omitidos caem no estilo EFETIVO do clipe (não só no do Job).
    style = ClipCaptionStyle(**{**resolve_caption_style(clip, job).model_dump(), **style_overrides})
    base_revision = clip.revision or 0
    new_path, text = render_plan(clip, video, job, plan, style)
    style_columns = {STYLE_COLUMNS[name]: value for name, value in style_overrides.items()}
    return save_plan_cas(db, clip, plan, new_path, text, style_columns, base_revision)
