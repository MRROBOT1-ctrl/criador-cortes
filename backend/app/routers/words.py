"""
GET /jobs/{job_id}/words (docs/api-contract-timeline-v1.md §3): palavras com
timestamp pro snap da timeline de revisão.

Só leitura. O transcript.json guarda o texto BRUTO do Whisper, então, com o
filtro de palavrões ligado ("cut"/"censor"), cada palavra passa pelo
redact_text antes de sair — o texto bruto nunca vaza pela API (spec §1.4).
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Job
from app.schemas import WordOut, WordsOut
from app.services import profanity
from app.services.clip_edit import CENSOR_MODES, r3

router = APIRouter(tags=["words"])

# Janela máxima por chamada (contrato §3.1). A janela da tela (clipe de até
# 60s + 20s de folga) cabe com sobra; trechos maiores vêm em chamadas
# seguidas, cada uma começando no `to` devolvido pela anterior.
MAX_WINDOW_SECONDS = 600.0

# O transcript não muda depois do processamento (contrato §3.4) — só no 200.
CACHE_CONTROL = "private, max-age=86400"


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


@router.get("/jobs/{job_id}/words", response_model=WordsOut)
def get_job_words(
    job_id: str,
    from_: float | None = Query(None, alias="from", description="Início da janela (s, absoluto). Default 0."),
    to: float | None = Query(None, description="Fim da janela (s, absoluto). Default from + 600."),
    db: Session = Depends(get_db),
):
    job = db.get(Job, job_id)
    if job is None:
        raise _error(404, "not_found", "Vídeo não encontrado.")

    # Ajuste da janela, NESTA ordem (contrato §3.1): defaults -> limites do
    # vídeo -> to ≤ from -> janela de 600s. Ajustar aos limites do vídeo não
    # conta como "truncated"; só o corte pela janela máxima conta.
    start = 0.0 if from_ is None else from_
    end = start + MAX_WINDOW_SECONDS if to is None else to
    duration = job.video.duration if job.video is not None else None
    start = max(0.0, start)
    end = max(0.0, end)
    if duration is not None:
        # Duração desconhecida (vídeo sem ffprobe ainda): só o limite 0 vale.
        start = min(start, duration)
        end = min(end, duration)
    if end <= start:
        raise _error(400, "invalid_range", "O fim da janela precisa ser maior que o início.")
    truncated = False
    if end - start > MAX_WINDOW_SECONDS:
        end = start + MAX_WINDOW_SECONDS
        truncated = True

    transcript_path = settings.data_dir / job_id / "transcript.json"
    if not transcript_path.exists():
        if job.status in ("pending", "running"):
            raise _error(409, "transcription_not_ready", "A transcrição deste vídeo ainda está em andamento.")
        raise _error(404, "transcript_not_found", "Este vídeo não tem transcrição salva.")
    data = json.loads(transcript_path.read_text(encoding="utf-8"))

    censored = (job.profanity_filter or "off") in CENSOR_MODES
    words: list[WordOut] = []
    # transcript.json antigo (antes de word_timestamps) não tem "words":
    # sai `words: []`, um 200 válido, e o frontend desliga o snap em silêncio.
    for seg in data.get("segments", []):
        for w in seg.get("words", []) or []:
            w_start, w_end = float(w["start"]), float(w["end"])
            if not (w_end > start and w_start < end):
                continue
            raw = str(w.get("text", "")).strip()  # o Whisper põe espaço na frente da palavra
            text = profanity.redact_text(raw) if censored else raw
            words.append(WordOut(start=r3(w_start), end=r3(w_end), text=text, is_censored=censored and text != raw))
    words.sort(key=lambda w: (w.start, w.end))

    out = WordsOut(
        job_id=job.id,
        language=job.language or data.get("language") or None,
        from_=r3(start),
        to=r3(end),
        truncated=truncated,
        censored=censored,
        words=words,
    )
    # JSONResponse direto pra poder pôr o Cache-Control só no 200 (os erros
    # acima sobem como HTTPException, sem o cabeçalho). by_alias: `from_` sai
    # como "from".
    return JSONResponse(content=out.model_dump(mode="json", by_alias=True), headers={"Cache-Control": CACHE_CONTROL})
