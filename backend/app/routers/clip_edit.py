"""
Timeline de Revisão v1 (docs/api-contract-timeline-v1.md §1 e §2):

- GET /clips/{clip_id}/draft — tudo o que a tela de revisão precisa numa
  chamada só, em tempos ABSOLUTOS (substitui as 4 chamadas da tela antiga).
- PUT /clips/{clip_id}/edit (?dry_run=true) — recebe o rascunho completo e
  renderiza UMA vez.

O router é fino de propósito: regra, remapeamento, censura, lock e
compare-and-swap ficam em app/services/clip_edit.py (o mesmo módulo que o
recut usa). Aqui só traduz EditError pro envelope de erro novo
({"detail": {"code", "message", ...}}, contrato §0.2) e serializa a saída.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Clip, Job, Video
from app.routers.download import _clip_to_out
from app.schemas import ClipDraftOut, ClipEditIn, ClipEditOut, ClipOut
from app.services import clip_edit
from app.services.clip_edit import EditError, r3

router = APIRouter(tags=["clip_edit"])


def _http_error(exc: EditError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.to_detail())


def _rounded_clip_out(clip: Clip) -> ClipOut:
    # Contrato §0.1: toda resposta dos endpoints novos com tempos em no
    # máximo 3 casas. O _clip_to_out (compartilhado com os endpoints antigos)
    # devolve o valor bruto do banco, então arredonda só aqui.
    out = _clip_to_out(clip)
    return out.model_copy(update={
        "start_time": r3(out.start_time),
        "end_time": r3(out.end_time),
        "video_duration": r3(out.video_duration) if out.video_duration is not None else None,
    })


@router.get("/clips/{clip_id}/draft", response_model=ClipDraftOut)
def get_clip_draft(clip_id: str, db: Session = Depends(get_db)):
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Corte não encontrado."})
    job = db.get(Job, clip.job_id)
    video = db.get(Video, clip.video_id)
    return clip_edit.draft_from_clip(clip, job, video)


@router.put("/clips/{clip_id}/edit", response_model=ClipEditOut)
def edit_clip(
    clip_id: str,
    payload: ClipEditIn,
    dry_run: bool = Query(False, description="Valida e calcula sem renderizar nem gravar."),
    db: Session = Depends(get_db),
):
    try:
        outcome = clip_edit.run_edit(db, clip_id, payload, dry_run)
    except EditError as exc:
        raise _http_error(exc) from exc

    # Montado à mão (e não via response_model) por causa do EditChange: o
    # contrato quer `text`/`change` AUSENTES quando não se aplicam, mas o
    # exclude_none só pode valer dentro dele — no resto (video_duration,
    # render_seconds: null no dry_run, language...) null é valor esperado.
    # O response_model continua declarado pra documentação do /openapi.json.
    body = {
        "status": outcome.status,
        "clip": _rounded_clip_out(outcome.clip).model_dump(mode="json"),
        "draft": outcome.draft.model_dump(mode="json"),
        "adjustments": [clip_edit.change_to_json(c) for c in outcome.adjustments],
        "render_seconds": outcome.render_seconds,
    }
    return JSONResponse(content=body)
