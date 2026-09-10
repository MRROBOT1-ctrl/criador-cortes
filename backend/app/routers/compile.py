"""
Timeline multi-clipe de um job: reordenar os clipes gerados e compilar um
subconjunto (na ordem escolhida) num único vídeo, sem reprocessar nada do
zero — reaproveita os .mp4 já renderizados por clipe (ver
app/services/video_cutter.py::cut_and_render_vertical) e só concatena.
"""
import json
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Clip, Job
from app.routers.download import _clip_to_out, _ranged_file_response
from app.schemas import ClipOut
from app.services import video_cutter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["compile"])


class ReorderRequest(BaseModel):
    clip_ids: list[str]


class CompileRequest(BaseModel):
    clip_ids: list[str]


class CompileResponse(BaseModel):
    path: str
    download_url: str


# Nome fixo pro compilado de cada job (um por job, não um por seleção) — evita
# acumular arquivo temporário a cada clique em "gerar" com uma seleção
# ligeiramente diferente. O `compiled.json` ao lado guarda QUAL seleção/ordem
# gerou esse arquivo, pra saber se precisa reconstruir (ver `_needs_rebuild`).
_COMPILED_FILENAME = "compiled.mp4"
_MANIFEST_FILENAME = "compiled.json"


def _compiled_paths(job_id: str) -> tuple[Path, Path]:
    job_dir = settings.output_dir / job_id
    return job_dir / _COMPILED_FILENAME, job_dir / _MANIFEST_FILENAME


def _needs_rebuild(compiled_path: Path, manifest_path: Path, clip_ids: list[str], clip_paths: list[Path]) -> bool:
    """Só reconstrói o compilado se a seleção/ordem pedida for diferente da
    que gerou o arquivo atual, ou se algum clipe envolvido for mais novo que
    ele (ex: usuário reeditou/recortou um clipe depois de já ter compilado) —
    mesmo padrão do zip em download.py::download_all, adaptado porque aqui
    também importa QUAL subconjunto/ordem foi usado, não só "algo mudou"."""
    if not compiled_path.exists() or not manifest_path.exists():
        return True
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    if manifest.get("clip_ids") != clip_ids:
        return True
    compiled_mtime = compiled_path.stat().st_mtime
    return any(p.stat().st_mtime > compiled_mtime for p in clip_paths)


@router.put("/jobs/{job_id}/clips/order", response_model=list[ClipOut])
def reorder_clips(job_id: str, payload: ReorderRequest, db: Session = Depends(get_db)):
    """Reatribui `Clip.index` conforme a ordem enviada — usado pelo
    drag-and-drop da timeline no frontend. O conjunto de ids precisa bater
    EXATAMENTE com os clipes existentes do job (nada faltando, nada
    estranho), senão um id apagado por engano deixaria o índice de outro
    clipe indefinido."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job não encontrado.")

    existing_by_id = {clip.id: clip for clip in job.clips}
    if len(payload.clip_ids) != len(set(payload.clip_ids)):
        raise HTTPException(status_code=400, detail="clip_ids contém ids repetidos.")
    if set(payload.clip_ids) != set(existing_by_id.keys()):
        raise HTTPException(
            status_code=400,
            detail="clip_ids deve conter exatamente todos os clipes do job, sem faltar nem sobrar nenhum.",
        )

    for new_index, clip_id in enumerate(payload.clip_ids):
        existing_by_id[clip_id].index = new_index
    db.commit()

    clips = sorted(existing_by_id.values(), key=lambda c: c.index)
    return [_clip_to_out(c) for c in clips]


@router.post("/jobs/{job_id}/compile", response_model=CompileResponse)
def compile_job_clips(job_id: str, payload: CompileRequest, db: Session = Depends(get_db)):
    """Concatena os clipes indicados (na ordem da lista, que pode ser um
    subconjunto dos clipes do job) num único mp4. Ver
    `video_cutter.concat_clips` pra estratégia de concat (copy rápido com
    fallback pra reencode)."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job não encontrado.")

    if not payload.clip_ids:
        raise HTTPException(status_code=400, detail="clip_ids não pode ser vazio.")
    if len(payload.clip_ids) != len(set(payload.clip_ids)):
        raise HTTPException(status_code=400, detail="clip_ids contém ids repetidos.")

    clips_by_id = {clip.id: clip for clip in job.clips}
    unknown_ids = [cid for cid in payload.clip_ids if cid not in clips_by_id]
    if unknown_ids:
        raise HTTPException(
            status_code=400,
            detail=f"clip_ids contém ids que não pertencem a este job: {unknown_ids}.",
        )

    ordered_clips: list[Clip] = [clips_by_id[cid] for cid in payload.clip_ids]
    missing = [
        c.id for c in ordered_clips
        if not c.processed_path or not Path(c.processed_path).exists()
    ]
    if missing:
        raise HTTPException(
            status_code=409,
            detail=f"Clipes ainda sem arquivo processado, não dá pra compilar: {missing}.",
        )

    clip_paths = [Path(c.processed_path) for c in ordered_clips]  # type: ignore[arg-type]
    compiled_path, manifest_path = _compiled_paths(job_id)
    compiled_path.parent.mkdir(parents=True, exist_ok=True)

    if _needs_rebuild(compiled_path, manifest_path, payload.clip_ids, clip_paths):
        video_cutter.concat_clips(clip_paths, compiled_path, tmp_dir=compiled_path.parent)
        manifest_path.write_text(
            json.dumps({"clip_ids": payload.clip_ids, "built_at": datetime.utcnow().isoformat()}),
            encoding="utf-8",
        )
    else:
        logger.info("Compilado do job %s já está atualizado pra essa seleção — não reconstruiu.", job_id)

    return CompileResponse(path=str(compiled_path), download_url=f"/jobs/{job_id}/compiled")


@router.get("/jobs/{job_id}/compiled")
def get_compiled_video(job_id: str, request: Request, db: Session = Depends(get_db)):
    """Serve o vídeo compilado mais recente do job. Usa `_ranged_file_response`
    (mesma função de download.py) pra permitir seek no player de preview do
    frontend, do mesmo jeito que o vídeo original já suporta."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job não encontrado.")

    compiled_path, _ = _compiled_paths(job_id)
    if not compiled_path.exists():
        raise HTTPException(status_code=404, detail="Ainda não foi gerado nenhum compilado para este job.")

    return _ranged_file_response(compiled_path, request, filename="compilado.mp4")
