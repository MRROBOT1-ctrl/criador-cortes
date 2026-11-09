"""
Infraestrutura da suíte do backend (Timeline de Revisão v1).

ISOLAMENTO: as variáveis abaixo PRECISAM ser definidas antes do primeiro
`import app...`, porque `app.config.settings` e a engine do SQLAlchemy são
criados na importação. Valores do ambiente têm prioridade sobre o `.env`, e o
guarda logo depois dos imports aborta a execução se algum caminho ainda for o
real. Nenhum teste chama o ffmpeg, o Redis ou o Celery.
"""
import atexit
import json
import os
import shutil
import sys
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
TEST_ROOT = Path(tempfile.mkdtemp(prefix="criador-cortes-tests-")).resolve()
atexit.register(shutil.rmtree, TEST_ROOT, ignore_errors=True)

os.environ.update(
    {
        "DATABASE_URL": f"sqlite:///{TEST_ROOT / 'test.db'}",
        "DATA_DIR": str(TEST_ROOT / "data"),
        "OUTPUT_DIR": str(TEST_ROOT / "outputs"),
        "UPLOAD_DIR": str(TEST_ROOT / "uploads"),
        "RELAXING_VIDEOS_DIR": str(TEST_ROOT / "relaxing_videos"),
        # Porta inválida de propósito: se algo tentar enfileirar, falha em vez
        # de cair no worker real.
        "REDIS_URL": "redis://127.0.0.1:1/15",
    }
)
sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Clip, Job, Video  # noqa: E402
from app.services import clip_rendering, video_cutter  # noqa: E402


def _abort_if_not_isolated() -> None:
    paths = {
        "data_dir": settings.data_dir,
        "output_dir": settings.output_dir,
        "upload_dir": settings.upload_dir,
        "relaxing_videos_dir": settings.relaxing_videos_dir,
        "database": Path(engine.url.database or ""),
    }
    for name, path in paths.items():
        if not Path(path).resolve().is_relative_to(TEST_ROOT):
            pytest.exit(f"ISOLAMENTO FALHOU: {name} aponta para {path}. Nada foi executado.", returncode=3)


_abort_if_not_isolated()

# ---------------------------------------------------------------------------
# Transcript de referência. O clipe padrão é a janela [10, 30] de um vídeo de
# 120s. Os palavrões têm tempos explícitos para as contas de mute/corte.
# ---------------------------------------------------------------------------


def _even_words(start: float, end: float, text: str) -> list[dict]:
    tokens = text.split()
    step = (end - start) / len(tokens)
    return [
        {"start": start + i * step, "end": start + (i + 1) * step, "text": f" {token}"}
        for i, token in enumerate(tokens)
    ]


def _segment(start: float, end: float, text: str, words: list[dict] | None = None) -> dict:
    return {"start": start, "end": end, "text": text, "words": words or _even_words(start, end, text)}


TRANSCRIPT: dict[str, Any] = {
    "language": "pt",
    "segments": [
        _segment(2.0, 6.0, "bem antes do corte"),
        _segment(7.0, 9.5, "logo antes do corte"),
        _segment(11.0, 14.0, "olá pessoal tudo bem"),
        _segment(
            15.0, 18.0, "que porra é essa",
            [
                # O espaço na frente imita o formato bruto do Whisper.
                {"start": 15.0, "end": 15.5, "text": " que"},
                {"start": 15.6, "end": 16.1, "text": " porra"},
                {"start": 16.2, "end": 16.4, "text": " é"},
                {"start": 16.5, "end": 18.0, "text": " essa"},
            ],
        ),
        _segment(20.0, 24.0, "isso é incrível"),
        _segment(26.0, 29.0, "vamos continuar agora"),
        _segment(
            32.0, 36.0, "essa merda aqui fora",
            [
                {"start": 32.0, "end": 32.9, "text": " essa"},
                {"start": 33.0, "end": 33.5, "text": " merda"},
                {"start": 33.6, "end": 34.5, "text": " aqui"},
                {"start": 34.6, "end": 36.0, "text": " fora"},
            ],
        ),
        _segment(40.0, 44.0, "depois do corte"),
    ],
}

DEFAULT_STYLE = {
    "enabled": True,
    "font_size": 8,
    "color": "#FFFFFF",
    "preset": "bold_outline",
    "font_family": "DejaVu Sans",
}

ORIGINAL_CLIP_BYTES = b"render-original"

# ---------------------------------------------------------------------------
# Renderizador falso: substitui SÓ o ffmpeg. render_clip, safe_rerender, o .srt
# temporário, a troca atômica e o nome _r{n} continuam sendo o código real.
# ---------------------------------------------------------------------------


@dataclass
class RenderCall:
    number: int
    start: float
    end: float
    output_path: Path
    srt_text: str | None
    kwargs: dict[str, Any]


class FakeRenderer:
    def __init__(self) -> None:
        self.calls: list[RenderCall] = []
        self.error: Exception | None = None
        self.hook: Callable[[RenderCall], None] | None = None
        self._guard = threading.Lock()

    def __call__(self, video_path, start, end, output_path, srt_path=None, **kwargs) -> None:
        srt_text = Path(srt_path).read_text(encoding="utf-8") if srt_path else None
        with self._guard:
            call = RenderCall(len(self.calls) + 1, start, end, Path(output_path), srt_text, kwargs)
            self.calls.append(call)
        if self.hook is not None:
            self.hook(call)
        if self.error is not None:
            raise self.error
        Path(output_path).write_bytes(f"render-{call.number}".encode())

    @property
    def last(self) -> RenderCall:
        return self.calls[-1]


class RenderGate:
    """Segura o render aberto, para simular requisições simultâneas sem sleep."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, _call: RenderCall) -> None:
        self.entered.set()
        if not self.release.wait(30):
            raise TimeoutError("RenderGate não foi liberado")


@dataclass
class Scenario:
    clip_id: str
    job_id: str
    video_id: str
    video_path: Path
    output_dir: Path
    data_dir: Path
    clip_path: Path

    @property
    def edit_url(self) -> str:
        return f"/clips/{self.clip_id}/edit"

    @property
    def draft_url(self) -> str:
        return f"/clips/{self.clip_id}/draft"

    @property
    def recut_url(self) -> str:
        return f"/clips/{self.clip_id}/recut"

    @property
    def words_url(self) -> str:
        return f"/jobs/{self.job_id}/words"

    @property
    def file_url(self) -> str:
        return f"/clips/{self.clip_id}/file"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_state():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    clip_rendering._busy_clips.clear()
    engine.dispose()
    for directory in (settings.data_dir, settings.output_dir, settings.upload_dir):
        shutil.rmtree(directory, ignore_errors=True)
        directory.mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def fake_render(monkeypatch) -> FakeRenderer:
    renderer = FakeRenderer()
    monkeypatch.setattr(video_cutter, "cut_and_render_vertical", renderer)
    return renderer


@pytest.fixture
def render_gate(fake_render):
    gate = RenderGate()
    fake_render.hook = gate
    yield gate
    gate.release.set()


@pytest.fixture
def client() -> TestClient:
    # Sem o `with`: o startup chamaria init_db, que clean_state já cobre.
    # raise_server_exceptions=False para um 500 voltar como resposta HTTP.
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def make_clip() -> Callable[..., Scenario]:
    def _make(
        *,
        start: float = 10.0,
        end: float = 30.0,
        profanity: str = "off",
        duration: float | None = 120.0,
        transcript: dict | None = TRANSCRIPT,
        job_status: str = "completed",
        job_language: str | None = None,
        rendered: bool = True,
        video_exists: bool = True,
        streamer_region: str | None = None,
        override: list[dict] | None = None,
        pan_rel: list[tuple[float, float]] | None = None,
        removed_rel: list[tuple[float, float]] | None = None,
        cut_rel: list[tuple[float, float]] | None = None,
        **clip_columns: Any,
    ) -> Scenario:
        job_id, video_id, clip_id = (uuid.uuid4().hex for _ in range(3))

        upload_dir = settings.upload_dir / job_id
        output_dir = settings.output_dir / job_id
        data_dir = settings.data_dir / job_id
        for directory in (upload_dir, output_dir, data_dir):
            directory.mkdir(parents=True, exist_ok=True)

        video_path = upload_dir / "video.mp4"
        if video_exists:
            video_path.write_bytes(b"video-original")
        clip_path = output_dir / "clip_0.mp4"
        if rendered:
            clip_path.write_bytes(ORIGINAL_CLIP_BYTES)
        if transcript is not None:
            (data_dir / "transcript.json").write_text(json.dumps(transcript), encoding="utf-8")

        db = SessionLocal()
        try:
            db.add(Video(id=video_id, filename="video.mp4", filepath=str(video_path), duration=duration))
            db.flush()
            db.add(
                Job(
                    id=job_id,
                    video_id=video_id,
                    status=job_status,
                    language=job_language,
                    profanity_filter=profanity,
                    streamer_region=streamer_region,
                    watermark_enabled=False,
                )
            )
            db.flush()
            clip = Clip(
                id=clip_id,
                video_id=video_id,
                job_id=job_id,
                index=0,
                start_time=start,
                end_time=end,
                score=50.0,
                processed_path=str(clip_path) if rendered else None,
                transcript_text="texto original",
                revision=0,
                **clip_columns,
            )
            clip.set_caption_segments_override(override)
            clip.set_manual_pan_keyframes(pan_rel)
            clip.set_manual_removed_ranges(removed_rel)
            clip.set_cut_segments(cut_rel)
            db.add(clip)
            db.commit()
        finally:
            db.close()

        return Scenario(clip_id, job_id, video_id, video_path, output_dir, data_dir, clip_path)

    return _make


@pytest.fixture
def scenario(make_clip) -> Scenario:
    return make_clip()


@pytest.fixture
def edit_body() -> Callable[..., dict]:
    """Corpo completo do PUT /edit. Linhas, zoom e removidos em tuplas ABSOLUTAS."""

    def _body(
        *,
        revision: int = 0,
        start: float = 10.0,
        end: float = 30.0,
        lines: list[tuple[float, float, str]] | None = None,
        pan: list[tuple[float, float]] | None = None,
        removed: list[tuple[float, float]] | None = None,
        style: dict | None = None,
        confirm_losses: bool | None = None,
    ) -> dict:
        body: dict[str, Any] = {
            "revision": revision,
            "range": {"start": start, "end": end},
            "caption_style": {**DEFAULT_STYLE, **(style or {})},
            "captions": {
                "mode": "auto" if lines is None else "manual",
                "lines": [{"start": s, "end": e, "text": t} for s, e, t in (lines or [])],
            },
            "pan": [{"t": t, "cx": cx} for t, cx in (pan or [])],
            "removed": [{"start": s, "end": e} for s, e in (removed or [])],
        }
        if confirm_losses is not None:
            body["confirm_losses"] = confirm_losses
        return body

    return _body


@pytest.fixture
def read_clip() -> Callable[[str], Clip]:
    def _read(clip_id: str) -> Clip:
        db = SessionLocal()
        try:
            clip = db.get(Clip, clip_id)
            db.expunge(clip)
            return clip
        finally:
            db.close()

    return _read


@pytest.fixture
def snapshot(read_clip) -> Callable[[Scenario], dict]:
    """Todas as colunas do clipe + todos os arquivos e bytes da pasta dele."""

    def _snapshot(sc: Scenario) -> dict:
        clip = read_clip(sc.clip_id)
        return {
            "columns": {c.name: getattr(clip, c.name) for c in Clip.__table__.columns},
            "files": {p.name: p.read_bytes() for p in sorted(sc.output_dir.iterdir()) if p.is_file()},
        }

    return _snapshot


@pytest.fixture
def set_revision() -> Callable[[str, int], None]:
    """Muda a revision por outra sessão, como faria um segundo processo da API."""

    def _set(clip_id: str, value: int) -> None:
        db = SessionLocal()
        try:
            db.get(Clip, clip_id).revision = value
            db.commit()
        finally:
            db.close()

    return _set
