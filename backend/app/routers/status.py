"""Consulta de status de um job — via REST (polling) ou WebSocket (push)."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models import Job
from app.schemas import JobOut

router = APIRouter(tags=["status"])


@router.get("/status/{job_id}", response_model=JobOut)
def get_status(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job não encontrado.")
    return job


@router.websocket("/ws/status/{job_id}")
async def ws_status(websocket: WebSocket, job_id: str):
    """Envia o status do job a cada segundo até ele terminar (completed/failed)
    ou o cliente desconectar. MVP simples via polling no banco — dado o volume
    baixo de jobs simultâneos, não precisa de pub/sub dedicado ainda."""
    await websocket.accept()
    last_payload = None
    try:
        while True:
            db = SessionLocal()
            try:
                job = db.get(Job, job_id)
            finally:
                db.close()

            if job is None:
                await websocket.send_json({"error": "Job não encontrado."})
                break

            payload = JobOut.model_validate(job).model_dump(mode="json")
            if payload != last_payload:
                await websocket.send_json(payload)
                last_payload = payload

            if job.status in ("completed", "failed"):
                break

            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        pass
