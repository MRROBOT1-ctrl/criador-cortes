"""Ponto de entrada da API FastAPI."""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db
from app.routers import clips, download, metrics, status, upload, watermark

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

app = FastAPI(title="VideoCutterAI", version="0.1.0")

# MVP local — sem frontend definitivo ainda, libera geral. Restringir quando
# o domínio do frontend React for definido.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload.router)
app.include_router(status.router)
app.include_router(download.router)
app.include_router(metrics.router)
app.include_router(clips.router)
app.include_router(watermark.router)

# Preview dos cortes prontos direto pelo navegador (<video src="/outputs/...">)
app.mount("/outputs", StaticFiles(directory=str(settings.output_dir)), name="outputs")

# Telinha simples de gráfico de métricas (HTML + Chart.js) — ver backend/static/metrics.html.
app.mount("/ui", StaticFiles(directory=str(settings.base_dir / "static"), html=True), name="ui")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/health")
def health():
    return {"status": "ok"}
