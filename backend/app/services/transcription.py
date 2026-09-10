"""
Transcrição de áudio com faster-whisper.

Roda em CPU (esta máquina não tem GPU), modelo "small" com compute_type
int8 — bom equilíbrio entre velocidade e qualidade pra esse cenário.
"""
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from faster_whisper import WhisperModel

from app.config import settings

logger = logging.getLogger(__name__)

_model: WhisperModel | None = None


def _get_model() -> WhisperModel:
    """Carrega o modelo Whisper uma única vez por processo (é pesado pra instanciar)."""
    global _model
    if _model is None:
        logger.info(
            "Carregando modelo Whisper '%s' (device=%s, compute_type=%s)...",
            settings.whisper_model_size,
            settings.whisper_device,
            settings.whisper_compute_type,
        )
        _model = WhisperModel(
            settings.whisper_model_size,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
        )
    return _model


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class TranscriptionResult:
    language: str
    full_text: str
    segments: list[Segment] = field(default_factory=list)


def transcribe(audio_path: Path, language: str | None = None) -> TranscriptionResult:
    """Transcreve o áudio e retorna os segmentos com timestamps.

    language=None deixa o Whisper autodetectar o idioma.
    """
    model = _get_model()
    try:
        segments_iter, info = model.transcribe(
            str(audio_path),
            language=language,
            vad_filter=True,  # remove trechos de silêncio, melhora precisão dos timestamps
        )
        segments_iter = list(segments_iter)  # força a iteração aqui, dentro do try
    except ValueError:
        # O VAD do faster-whisper pode remover o áudio inteiro quando não há
        # fala detectável (trilha só com música/efeitos, silêncio prolongado
        # etc.) — nesse caso a detecção de idioma quebra internamente com
        # "max() arg is an empty sequence". Trata como "sem fala": segue o
        # pipeline com transcrição vazia em vez de derrubar o job inteiro
        # (a seleção de melhores momentos ainda funciona só com o volume).
        logger.warning("Nenhuma fala detectável em %s — seguindo sem transcrição.", audio_path)
        return TranscriptionResult(language=language or "und", full_text="", segments=[])

    segments: list[Segment] = []
    full_text_parts: list[str] = []
    for seg in segments_iter:
        text = seg.text.strip()
        segments.append(Segment(start=seg.start, end=seg.end, text=text))
        full_text_parts.append(text)

    return TranscriptionResult(
        language=info.language,
        full_text=" ".join(full_text_parts),
        segments=segments,
    )


def _format_srt_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    millis = int(round((secs - int(secs)) * 1000))
    return f"{int(hours):02d}:{int(minutes):02d}:{int(secs):02d},{millis:03d}"


def write_srt(segments: list[Segment], output_path: Path, offset: float = 0.0) -> None:
    """Gera um arquivo .srt a partir dos segmentos.

    `offset` desloca os timestamps — usado para gerar a legenda de um corte
    específico (tempos relativos ao início do corte, não do vídeo original).
    """
    lines: list[str] = []
    for i, seg in enumerate(segments, start=1):
        start = max(0.0, seg.start - offset)
        end = max(0.0, seg.end - offset)
        if end <= 0:
            continue
        lines.append(str(i))
        lines.append(f"{_format_srt_timestamp(start)} --> {_format_srt_timestamp(end)}")
        lines.append(seg.text)
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def load_transcript_segments(data_dir: Path) -> list[Segment]:
    """Carrega os segmentos já transcritos e salvos em disco
    (`data/{job_id}/transcript.json`) — usado quando algo (ex: um re-corte
    manual) precisa reconstituir o texto de um trecho sem rodar o Whisper de
    novo. Retorna lista vazia se o arquivo não existir."""
    transcript_path = data_dir / "transcript.json"
    if not transcript_path.exists():
        return []
    data = json.loads(transcript_path.read_text(encoding="utf-8"))
    return [Segment(start=s["start"], end=s["end"], text=s["text"]) for s in data.get("segments", [])]


def segments_overlapping(segments: list[Segment], start: float, end: float, min_overlap_ratio: float = 0.5) -> list[Segment]:
    """Filtra os segmentos cuja sobreposição com [start, end] cobre pelo
    menos `min_overlap_ratio` da duração do próprio segmento — evita vazar
    texto de frases que só tocam a borda do intervalo (mesmo critério usado
    em app/tasks/pipeline.py pra montar transcript_text/.srt de cada corte)."""
    def _overlaps_enough(seg: Segment) -> bool:
        overlap = max(0.0, min(seg.end, end) - max(seg.start, start))
        seg_duration = seg.end - seg.start
        return seg_duration > 0 and overlap >= min_overlap_ratio * seg_duration

    return [seg for seg in segments if _overlaps_enough(seg)]
