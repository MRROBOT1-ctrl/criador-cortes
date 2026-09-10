"""
Modelos SQLAlchemy.

Video   -> arquivo original enviado pelo usuário.
Job     -> uma execução do pipeline de processamento sobre um Video
           (guarda config do corte, progresso e logs).
Clip    -> cada corte vertical gerado a partir de um Job.
Metric  -> histórico de métricas de engajamento de um Clip já publicado
           (lançadas manualmente ou sincronizadas via API do YouTube).
"""
import json
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.database import Base


def gen_uuid() -> str:
    return uuid.uuid4().hex


class Video(Base):
    __tablename__ = "videos"

    id = Column(String, primary_key=True, default=gen_uuid)
    filename = Column(String, nullable=False)
    filepath = Column(String, nullable=False)
    duration = Column(Float, nullable=True)  # segundos; preenchido após inspeção via ffprobe
    status = Column(String, default="uploaded")  # uploaded | downloading | processing | done | failed
    # Preenchido quando o vídeo vem de um link (YouTube/TikTok/Instagram via
    # yt-dlp) em vez de upload de arquivo — None pro fluxo de upload normal.
    source_url = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    jobs = relationship("Job", back_populates="video", cascade="all, delete-orphan")


class Job(Base):
    __tablename__ = "jobs"

    # O id do Job é o mesmo nome da pasta uploads/{job_id}/ — facilita achar os arquivos.
    id = Column(String, primary_key=True, default=gen_uuid)
    video_id = Column(String, ForeignKey("videos.id"), nullable=False)

    status = Column(String, default="pending")  # pending|running|completed|failed
    progress = Column(Integer, default=0)  # 0-100
    current_phase = Column(String, nullable=True)
    logs = Column(Text, default="")  # linhas de log concatenadas, mais recente por último
    error_message = Column(Text, nullable=True)

    # Config de corte — por job (não por usuário/settings global, MVP não tem multi-user)
    max_clips = Column(Integer, default=3)
    clip_duration_min = Column(Integer, default=15)
    clip_duration_max = Column(Integer, default=60)
    keywords = Column(Text, nullable=True)  # CSV de palavras-chave configuradas pelo usuário
    language = Column(String, nullable=True)  # None = autodetectar

    # Marca d'água/logo — uma imagem só, global pro projeto inteiro (ver
    # app/routers/watermark.py), ligada/desligada e posicionada por job.
    watermark_enabled = Column(Boolean, default=False)
    watermark_position = Column(String, default="bottom-right")  # top-left|top-right|bottom-left|bottom-right

    # Estilo da legenda queimada no vídeo (ver app/services/video_cutter.py).
    # A posição vertical (MarginV) NÃO é configurável pelo usuário — fica fixa
    # por preset, já validada visualmente (ver comentário em video_cutter.py
    # sobre o bug de MarginV alto empurrando a legenda pra fora da tela).
    captions_enabled = Column(Boolean, default=True)  # opção de gerar sem legenda nenhuma
    caption_font_size = Column(Integer, default=8)
    caption_color = Column(String, default="#FFFFFF")  # hex #RRGGBB
    caption_style = Column(String, default="bold_outline")  # bold_outline|boxed|minimal|no_outline
    caption_font_family = Column(String, default="DejaVu Sans")

    # Janela do streamer detectada (ver app/services/face_detection.py),
    # persistida como "x,y,w,h" em pixels da resolução ORIGINAL do vídeo —
    # NULL se não detectou nada. Guardada aqui (em vez de só em memória
    # durante o processamento) pra que um re-corte manual (POST
    # /clips/{id}/recut) possa reaplicar o mesmo layout de tela dividida em
    # vez de reverter silenciosamente pro crop central.
    streamer_region = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    video = relationship("Video", back_populates="jobs")
    clips = relationship("Clip", back_populates="job", cascade="all, delete-orphan")

    def add_log(self, message: str) -> None:
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        self.logs = (self.logs or "") + f"[{ts}] {message}\n"

    def set_streamer_region(self, region: tuple[int, int, int, int] | None) -> None:
        self.streamer_region = ",".join(str(v) for v in region) if region else None

    def get_streamer_region(self) -> tuple[int, int, int, int] | None:
        if not self.streamer_region:
            return None
        try:
            x, y, w, h = (int(v) for v in self.streamer_region.split(","))
            return (x, y, w, h)
        except (ValueError, TypeError):
            return None


class Clip(Base):
    __tablename__ = "clips"

    id = Column(String, primary_key=True, default=gen_uuid)
    video_id = Column(String, ForeignKey("videos.id"), nullable=False)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=False)

    index = Column(Integer, default=0)  # posição/ordem entre os cortes do job (0, 1, 2...)
    start_time = Column(Float, nullable=False)  # segundos, no vídeo original
    end_time = Column(Float, nullable=False)
    score = Column(Float, default=0.0)  # pontuação 0-100 usada para ranquear o momento

    processed_path = Column(String, nullable=True)  # caminho do mp4 final (vertical + legenda)
    transcript_text = Column(Text, nullable=True)  # texto transcrito do trecho (p/ hashtags/UI)
    hashtags = Column(Text, nullable=True)  # JSON: {"tiktok": [...], "instagram_reels": [...], ...}

    # Rastro de pan MANUAL (ver app/services/video_cutter.py::_build_pan_crop_x_expr) —
    # JSON: [{"t": segundos_desde_o_inicio_do_clipe, "cx": fracao_0_a_1_da_largura_do_video_ORIGINAL}, ...].
    # NULL = sem override, usa a detecção automática de rosto de sempre
    # (app/services/face_detection.py::track_horizontal_pan) ou o crop
    # central se ela também não achar nada. Só se aplica a vídeo SOLO (sem
    # streamer_region — ver Job.streamer_region); não há UI/suporte ainda
    # pra combinar pan manual com o layout de tela dividida.
    manual_pan_keyframes = Column(Text, nullable=True)

    # Legenda editada manualmente pelo usuário (ver app/routers/captions.py) —
    # JSON: [{"start": segundos_desde_o_inicio_do_clipe, "end": ..., "text": "..."}, ...].
    # NULL = sem override, a legenda continua vindo 100% da transcrição do
    # Whisper (data/{job_id}/transcript.json filtrada por segments_overlapping
    # + offset, como sempre foi). Guardado com os mesmos tempos RELATIVOS AO
    # CLIPE usados em manual_pan_keyframes (não no vídeo original) — assim um
    # recut que muda o intervalo (ver clips.py::recut_clip) sabe que precisa
    # invalidar isso do mesmo jeito que já invalida o pan manual.
    caption_segments_override = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    job = relationship("Job", back_populates="clips")
    metrics = relationship("Metric", back_populates="clip", cascade="all, delete-orphan")

    def set_manual_pan_keyframes(self, keyframes: list[tuple[float, float]] | None) -> None:
        self.manual_pan_keyframes = json.dumps(keyframes) if keyframes else None

    def get_manual_pan_keyframes(self) -> list[tuple[float, float]] | None:
        if not self.manual_pan_keyframes:
            return None
        try:
            return [(float(t), float(cx)) for t, cx in json.loads(self.manual_pan_keyframes)]
        except (ValueError, TypeError, json.JSONDecodeError):
            return None

    def set_caption_segments_override(self, segments: list[dict] | None) -> None:
        # Lista vazia é tratada igual a None (remove o override) — quem chama
        # (PUT /clips/{id}/captions) já normaliza isso, mas fica seguro aqui
        # também caso outro código venha a usar esse setter no futuro.
        self.caption_segments_override = json.dumps(segments) if segments else None

    def get_caption_segments_override(self) -> list[dict] | None:
        if not self.caption_segments_override:
            return None
        try:
            data = json.loads(self.caption_segments_override)
            return [{"start": float(s["start"]), "end": float(s["end"]), "text": str(s["text"])} for s in data]
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None


class Metric(Base):
    __tablename__ = "metrics"

    id = Column(String, primary_key=True, default=gen_uuid)
    clip_id = Column(String, ForeignKey("clips.id"), nullable=False)

    platform = Column(String, nullable=False)  # tiktok|instagram_reels|youtube_shorts|facebook_reels
    views = Column(Integer, default=0)
    likes = Column(Integer, default=0)
    comments = Column(Integer, default=0)
    shares = Column(Integer, default=0)

    source = Column(String, default="manual")  # manual | youtube_api
    external_video_id = Column(String, nullable=True)  # id do vídeo na plataforma (p/ sync)

    recorded_at = Column(DateTime, default=datetime.utcnow)

    clip = relationship("Clip", back_populates="metrics")
