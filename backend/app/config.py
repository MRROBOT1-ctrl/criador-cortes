"""
Configurações centrais da aplicação.

Tudo que pode variar entre dev/prod (paths, credenciais, parâmetros do Whisper)
vem de variáveis de ambiente, com defaults sensatos para rodar localmente.
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Diretórios de trabalho ---
    base_dir: Path = Path(__file__).resolve().parent.parent  # backend/
    upload_dir: Path = base_dir / "uploads"
    output_dir: Path = base_dir / "outputs"
    data_dir: Path = base_dir / "data"

    # --- Banco de dados ---
    database_url: str = "sqlite:///./data/app.db"

    # --- Redis / Celery ---
    redis_url: str = "redis://localhost:6379/0"

    # --- Whisper (faster-whisper) ---
    whisper_model_size: str = "small"  # base/small/medium — "small" é o equilíbrio p/ CPU
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"  # int8 é bem mais rápido em CPU que float32

    # --- Config default de corte (pode ser sobrescrita por Job) ---
    default_max_clips: int = 3
    default_clip_duration_min: int = 15
    default_clip_duration_max: int = 60

    # --- Upload ---
    max_upload_size_gb: float = 5.0

    # --- Integrações externas (opcionais) ---
    youtube_api_key: str | None = None

    def ensure_dirs(self) -> None:
        for d in (self.upload_dir, self.output_dir, self.data_dir):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
