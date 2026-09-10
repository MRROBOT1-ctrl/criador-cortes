"""
Helpers compartilhados por qualquer endpoint que precise RE-renderizar um
clipe já existente (recut de tempo, edição de legenda, pan manual, etc.) —
extraído de app/routers/clips.py pra evitar duplicar a mesma lógica de
"resolve os defaults do job" e "renderiza com segurança" em cada endpoint novo.
"""
from dataclasses import dataclass
from pathlib import Path

from app.models import Job
from app.routers.watermark import find_watermark_file
from app.services import video_cutter


@dataclass
class JobRenderDefaults:
    """Parâmetros de renderização herdados do Job — usados como fallback
    quando um endpoint de edição não está mexendo naquele aspecto específico
    (ex: editar só a legenda não deve resetar marca d'água/streamer_region)."""

    watermark_path: Path | None
    watermark_position: str
    captions_enabled: bool
    caption_font_size: int
    caption_color: str
    caption_style: str
    caption_font_family: str
    streamer_region: tuple[int, int, int, int] | None


def resolve_job_render_defaults(job: Job | None) -> JobRenderDefaults:
    watermark_path = find_watermark_file() if (job is not None and job.watermark_enabled) else None
    return JobRenderDefaults(
        watermark_path=watermark_path,
        watermark_position=(job.watermark_position if job else None) or "bottom-right",
        captions_enabled=job.captions_enabled if (job and job.captions_enabled is not None) else True,
        caption_font_size=(job.caption_font_size if job else None) or 8,
        caption_color=(job.caption_color if job else None) or "#FFFFFF",
        caption_style=(job.caption_style if job else None) or "bold_outline",
        caption_font_family=(job.caption_font_family if job else None) or "DejaVu Sans",
        streamer_region=job.get_streamer_region() if job is not None else None,
    )


def safe_rerender(
    video_path: Path,
    start: float,
    end: float,
    output_path: Path,
    *,
    srt_path: Path | None = None,
    cleanup_srt: bool = False,
    **cutter_kwargs,
) -> None:
    """Renderiza num arquivo temporário e só substitui `output_path` se o
    ffmpeg terminar com sucesso — uma falha no meio do encode (disco cheio,
    processo morto) não deve sobrescrever o clipe anterior (que funcionava)
    com um arquivo truncado/corrompido. `cleanup_srt=True` apaga o .srt
    temporário depois (usado quando o chamador gerou um só pra essa
    renderização, não um arquivo persistente)."""
    tmp_path = output_path.with_suffix(".recut_tmp.mp4")
    try:
        video_cutter.cut_and_render_vertical(
            video_path, start, end, tmp_path, srt_path=srt_path, **cutter_kwargs,
        )
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    finally:
        if cleanup_srt and srt_path is not None:
            srt_path.unlink(missing_ok=True)
    tmp_path.replace(output_path)  # troca atômica (mesmo filesystem) só após sucesso
