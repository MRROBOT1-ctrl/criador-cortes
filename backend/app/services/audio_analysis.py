"""
Detecção simples de "melhores momentos" a partir do áudio + transcrição.

MVP: pontuação por janelas de tempo combinando
  - pico de volume (proxy de emoção/entusiasmo)
  - presença de palavras-chave configuradas pelo usuário
depois seleciona os top-N momentos sem sobreposição.

Análise visual (movimento/rosto) fica pra fase 2 (ver memória do projeto).
"""
from dataclasses import dataclass

import numpy as np
from pydub import AudioSegment

from app.services.transcription import Segment

WINDOW_SECONDS = 5.0  # granularidade da pontuação, conforme spec original


@dataclass
class ScoredWindow:
    start: float
    end: float
    volume_score: float  # 0-100
    keyword_score: float  # 0-100
    score: float  # combinado


def _rms_windows(audio_path, window_seconds: float = WINDOW_SECONDS) -> list[float]:
    """Retorna o RMS (volume) de cada janela de `window_seconds` do áudio."""
    audio = AudioSegment.from_file(audio_path)
    window_ms = int(window_seconds * 1000)
    duration_ms = len(audio)

    rms_values: list[float] = []
    for start_ms in range(0, duration_ms, window_ms):
        chunk = audio[start_ms:start_ms + window_ms]
        rms_values.append(chunk.rms if len(chunk) > 0 else 0.0)
    return rms_values


def _keyword_hits(segments: list[Segment], keywords: list[str], start: float, end: float) -> int:
    if not keywords:
        return 0
    hits = 0
    for seg in segments:
        if seg.end < start or seg.start > end:
            continue
        text_lower = seg.text.lower()
        hits += sum(1 for kw in keywords if kw.strip() and kw.strip().lower() in text_lower)
    return hits


def score_windows(
    audio_path,
    transcript_segments: list[Segment],
    keywords: list[str] | None = None,
    window_seconds: float = WINDOW_SECONDS,
) -> list[ScoredWindow]:
    """Pontua cada janela de `window_seconds` do vídeo de 0 a 100."""
    keywords = keywords or []
    rms_values = _rms_windows(audio_path, window_seconds)
    if not rms_values:
        return []

    max_rms = max(rms_values) or 1.0
    windows: list[ScoredWindow] = []

    for i, rms in enumerate(rms_values):
        start = i * window_seconds
        end = start + window_seconds

        volume_score = float(np.clip((rms / max_rms) * 100, 0, 100))
        kw_hits = _keyword_hits(transcript_segments, keywords, start, end)
        keyword_score = float(np.clip(kw_hits * 40, 0, 100))  # cada hit soma bastante

        # Pesos simplificados do MVP (spec original tinha mais fatores —
        # movimento/sentimento ficam pra fase 2): 60% volume, 40% palavras-chave.
        combined = 0.6 * volume_score + 0.4 * keyword_score

        windows.append(ScoredWindow(start, end, volume_score, keyword_score, combined))

    return windows


@dataclass
class CandidateClip:
    start: float
    end: float
    score: float


def pick_top_clips(
    windows: list[ScoredWindow],
    max_clips: int,
    clip_duration_min: float,
    clip_duration_max: float,
) -> list[CandidateClip]:
    """Expande as janelas de maior pontuação em cortes de duração válida,
    evitando sobreposição (non-maximum suppression bem simples)."""
    if not windows:
        return []

    total_duration = windows[-1].end
    sorted_windows = sorted(windows, key=lambda w: w.score, reverse=True)

    chosen: list[CandidateClip] = []

    def overlaps(a_start, a_end, b_start, b_end) -> bool:
        return a_start < b_end and b_start < a_end

    for w in sorted_windows:
        if len(chosen) >= max_clips:
            break

        # Centraliza o corte na janela de pico, respeitando a duração mínima configurada.
        target_duration = clip_duration_min
        center = (w.start + w.end) / 2
        start = max(0.0, center - target_duration / 2)
        end = min(total_duration, start + target_duration)
        start = max(0.0, end - target_duration)  # reajusta caso tenha batido no fim do vídeo

        if end - start < 1.0:
            continue  # sobrou um trecho residual minúsculo, não vale a pena
        if any(overlaps(start, end, c.start, c.end) for c in chosen):
            continue

        chosen.append(CandidateClip(start=start, end=min(end, start + clip_duration_max), score=w.score))

    chosen.sort(key=lambda c: c.start)
    return chosen


def snap_clip_ends_to_sentence(
    candidates: list[CandidateClip],
    transcript_segments: list[Segment],
    clip_duration_max: float,
    total_duration: float,
) -> list[tuple[CandidateClip, float]]:
    """Estende `candidate.end` até o fim da frase (segmento de transcrição)
    quando o corte termina no meio dela, evitando cortar a última palavra.

    Só estende se isso não ultrapassar `clip_duration_max` (a partir de
    `candidate.start`) nem invadir o início do próximo candidato (assume-se
    `candidates` já ordenado por `start`, como retornado por `pick_top_clips`).

    Retorna uma lista de tuplas (candidate, extended_seconds) na mesma ordem
    recebida — `extended_seconds` é 0.0 quando nada foi estendido.
    """
    result: list[tuple[CandidateClip, float]] = []

    for idx, candidate in enumerate(candidates):
        extended_seconds = 0.0

        # Segmento que contém o instante candidate.end no seu meio (não na borda).
        mid_segment = next(
            (seg for seg in transcript_segments if seg.start < candidate.end < seg.end),
            None,
        )

        if mid_segment is not None:
            new_end = min(mid_segment.end, total_duration)
            max_allowed_end = candidate.start + clip_duration_max
            next_candidate_start = (
                candidates[idx + 1].start if idx + 1 < len(candidates) else float("inf")
            )

            if new_end <= max_allowed_end and new_end <= next_candidate_start:
                extended_seconds = new_end - candidate.end
                candidate = CandidateClip(start=candidate.start, end=new_end, score=candidate.score)

        result.append((candidate, extended_seconds))

    return result
