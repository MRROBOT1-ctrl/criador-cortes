"""Schemas Pydantic — request/response da API."""
import re
from datetime import datetime

from pydantic import BaseModel, Field

from app.config import settings

# Estilo da legenda queimada (ver app/services/video_cutter.py) — a posição
# vertical (MarginV) fica fixa por preset, não é exposta ao usuário (ver
# comentário sobre o bug de MarginV alto empurrando a legenda pra fora da tela).
CAPTION_STYLES = {"bold_outline", "boxed", "minimal", "no_outline"}
# Fontes disponíveis na imagem Docker (ver Dockerfile — DejaVu vem na base
# Debian, Liberation* foi adicionada via apt fonts-liberation). Uma fonte
# fora dessa lista cairia num fallback silencioso do fontconfig, então
# valida contra essa allowlist em vez de aceitar qualquer string.
CAPTION_FONT_FAMILIES = {
    "DejaVu Sans", "DejaVu Serif", "DejaVu Sans Mono",
    "Liberation Sans", "Liberation Serif", "Liberation Mono",
}
CAPTION_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

# Filtro de palavrões (ver app/services/profanity.py e Job.profanity_filter):
# "off" nunca mexe em nada; "cut" remove o trecho da palavra do vídeo; "censor"
# muta o áudio da palavra e troca por asteriscos na legenda.
PROFANITY_MODES = {"off", "cut", "censor"}


def validate_profanity_filter(value: str) -> str:
    if value not in PROFANITY_MODES:
        raise ValueError(f"profanity_filter inválido: '{value}'. Use: {', '.join(sorted(PROFANITY_MODES))}.")
    return value


def validate_caption_color(value: str) -> str:
    if not CAPTION_COLOR_RE.match(value):
        raise ValueError(f"caption_color inválida: '{value}'. Use um hex no formato #RRGGBB (ex: #FFFFFF).")
    return value


def validate_caption_style(value: str) -> str:
    if value not in CAPTION_STYLES:
        raise ValueError(f"caption_style inválido: '{value}'. Use: {', '.join(sorted(CAPTION_STYLES))}.")
    return value


def validate_caption_font_family(value: str) -> str:
    if value not in CAPTION_FONT_FAMILIES:
        raise ValueError(f"caption_font_family inválida: '{value}'. Use: {', '.join(sorted(CAPTION_FONT_FAMILIES))}.")
    return value


class VideoOut(BaseModel):
    id: str
    filename: str
    duration: float | None
    status: str
    source_url: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class JobOut(BaseModel):
    id: str
    video_id: str
    status: str
    progress: int
    current_phase: str | None
    logs: str
    error_message: str | None
    max_clips: int
    clip_duration_min: int
    clip_duration_max: int
    keywords: str | None
    language: str | None
    watermark_enabled: bool
    watermark_position: str | None
    captions_enabled: bool
    caption_font_size: int
    caption_color: str
    caption_style: str
    caption_font_family: str
    use_relaxing_video_fallback: bool
    profanity_filter: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UploadResponse(BaseModel):
    video: VideoOut
    job: JobOut


class JobSummary(BaseModel):
    """Resumo de um Job pra listagem (GET /jobs) — combina campos do Job com
    dados do Video associado e a contagem de clipes, pra evitar que o
    frontend precise cruzar múltiplas chamadas só pra montar uma lista."""

    id: str
    status: str
    progress: int
    current_phase: str | None
    created_at: datetime
    video_filename: str
    video_duration: float | None
    clip_count: int


class ClipOut(BaseModel):
    id: str
    video_id: str
    job_id: str
    index: int
    start_time: float
    end_time: float
    score: float
    processed_path: str | None
    transcript_text: str | None
    hashtags: dict[str, list[str]] | None
    suggested_titles: list[str] | None
    video_duration: float | None  # duração do vídeo ORIGINAL — usado pelo editor (limite da timeline)
    has_streamer_region: bool  # job usa layout de tela dividida — zoom manual (POST /clips/{id}/pan) não suportado junto

    class Config:
        from_attributes = True


class MetricIn(BaseModel):
    platform: str = Field(description="tiktok | instagram_reels | youtube_shorts | facebook_reels")
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0


class MetricOut(BaseModel):
    id: str
    clip_id: str
    platform: str
    views: int
    likes: int
    comments: int
    shares: int
    source: str
    external_video_id: str | None
    recorded_at: datetime

    class Config:
        from_attributes = True


class YoutubeSyncRequest(BaseModel):
    youtube_video_id: str = Field(description="ID do vídeo no YouTube (11 chars da URL, ex: dQw4w9WgXcQ)")


class UrlUploadRequest(BaseModel):
    url: str = Field(description="Link do vídeo no YouTube, TikTok, Instagram etc. (baixado via yt-dlp)")
    max_clips: int = settings.default_max_clips
    clip_duration_min: int = settings.default_clip_duration_min
    clip_duration_max: int = settings.default_clip_duration_max
    keywords: str = ""
    language: str | None = None
    watermark_enabled: bool = False
    watermark_position: str = "bottom-right"
    # Validados manualmente no router (não via pydantic validator) pra dar um
    # 400 com mensagem clara em vez do 422 genérico do FastAPI — mesmo padrão
    # já usado ali pra watermark_position.
    captions_enabled: bool = True
    caption_font_size: int = 8
    caption_color: str = "#FFFFFF"
    caption_style: str = "bold_outline"
    caption_font_family: str = "DejaVu Sans"
    # "sem janela": quando False, um vídeo de narração pura (sem streamer,
    # sem rosto rastreável) sempre cai pro crop central simples de sempre —
    # nunca tenta o vídeo relaxante de fundo (ver app/services/video_cutter.py).
    use_relaxing_video_fallback: bool = True
    # Validado manualmente no router (mesmo padrão de watermark_position/
    # caption_style acima) — ver validate_profanity_filter.
    profanity_filter: str = "off"


class WatermarkStatus(BaseModel):
    exists: bool
