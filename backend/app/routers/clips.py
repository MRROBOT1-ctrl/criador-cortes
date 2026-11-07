"""
Edição de um clipe já gerado: reajustar o tempo do corte e/ou o estilo da
legenda (inclusive religá-la, trocar fonte/cor/formato, ou desligar de vez)
sem precisar reprocessar o vídeo inteiro (transcrição, seleção de momentos
etc. — só re-renderiza esse clipe específico com os novos parâmetros).
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Clip, Job, Video
from app.routers.download import _clip_to_out
from app.routers.upload import CAPTION_FONT_SIZE_MAX, CAPTION_FONT_SIZE_MIN
from app.schemas import (
    ClipOut,
    validate_caption_color,
    validate_caption_font_family,
    validate_caption_style,
)
from app.services import clip_edit
from app.services.clip_edit import EditError
from app.services.clip_rendering import clip_render_lock

router = APIRouter(tags=["clips"])


class RecutRequest(BaseModel):
    # Todos os campos são opcionais: None mantém o que o clipe/job já tinha.
    # Isso deixa o mesmo endpoint servir tanto pra "só ajustar o tempo" quanto
    # "só mudar o estilo da legenda sem mexer no corte" quanto os dois juntos.
    start_time: float | None = None
    end_time: float | None = None
    captions_enabled: bool | None = None
    caption_font_size: int | None = None
    caption_color: str | None = None
    caption_style: str | None = None
    caption_font_family: str | None = None


def _validate_caption_overrides(payload: RecutRequest) -> None:
    if payload.caption_font_size is not None and not (
        CAPTION_FONT_SIZE_MIN <= payload.caption_font_size <= CAPTION_FONT_SIZE_MAX
    ):
        raise HTTPException(
            status_code=400,
            detail=f"caption_font_size inválido ({payload.caption_font_size}). Use um valor entre "
            f"{CAPTION_FONT_SIZE_MIN} e {CAPTION_FONT_SIZE_MAX}.",
        )
    for value, validator in (
        (payload.caption_color, validate_caption_color),
        (payload.caption_style, validate_caption_style),
        (payload.caption_font_family, validate_caption_font_family),
    ):
        if value is None:
            continue
        try:
            validator(value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/clips/{clip_id}/recut", response_model=ClipOut)
def recut_clip(clip_id: str, payload: RecutRequest, db: Session = Depends(get_db)):
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clipe não encontrado.")

    # Lock por clipe (contrato §0.5): segurado durante validação + render +
    # gravação, e o clipe é relido logo depois de adquirido — assim tudo é
    # calculado em cima do estado ATUAL (outra renderização pode ter acabado
    # de gravar). Ocupado = 409 render_in_progress no envelope novo; os
    # demais erros deste endpoint continuam no formato antigo.
    with clip_render_lock(clip.id):
        db.refresh(clip)
        return _recut_locked(clip, payload, db)


def _recut_locked(clip: Clip, payload: RecutRequest, db: Session) -> ClipOut:
    start_time = payload.start_time if payload.start_time is not None else clip.start_time
    end_time = payload.end_time if payload.end_time is not None else clip.end_time
    if end_time <= start_time:
        raise HTTPException(status_code=400, detail="end_time deve ser maior que start_time.")
    _validate_caption_overrides(payload)

    video = db.get(Video, clip.video_id)
    if video is None or not video.filepath or not Path(video.filepath).exists():
        raise HTTPException(status_code=404, detail="Vídeo original não encontrado.")

    if video.duration is not None and (start_time < 0 or end_time > video.duration):
        raise HTTPException(
            status_code=400,
            detail=f"Intervalo fora da duração do vídeo original (0 - {video.duration:.2f}s).",
        )

    if not clip.processed_path:
        raise HTTPException(status_code=409, detail="Clipe ainda não possui um arquivo processado para sobrescrever.")

    job = db.get(Job, clip.job_id)

    # Estilo: só os campos ENVIADOS entram — eles são gravados nas colunas
    # caption_style_* do Clip (contrato §4.5); os omitidos não mexem na
    # coluna e, no render, caem no estilo EFETIVO do clipe (Clip > Job >
    # default). Antes o estilo do recut não era gravado em lugar nenhum, e
    # um PUT /pan logo depois voltava ao estilo do Job.
    style_overrides = {
        name: value
        for name, value in (
            ("enabled", payload.captions_enabled),
            ("font_size", payload.caption_font_size),
            ("color", payload.caption_color),
            ("preset", payload.caption_style),
            ("font_family", payload.caption_font_family),
        )
        if value is not None
    }

    # Mudar o tempo REMAPEIA em vez de apagar (spec §6, decisão de 06/11):
    # legenda manual, zoom manual e trechos removidos pelo usuário que
    # continuam dentro do corte novo sobrevivem com o MESMO tempo absoluto;
    # o que fica parcialmente fora é cortado na borda; o que fica totalmente
    # fora é descartado (a tela antiga não tem modal, então o recut age como
    # confirm_losses=true). Os palavrões são recalculados pra janela nova.
    # Tudo isso é a MESMA regra do PUT /edit — por isso mora em
    # app/services/clip_edit.py, e não aqui.
    try:
        swapped = clip_edit.run_recut(db, clip, video, job, start_time, end_time, style_overrides)
    except EditError as exc:
        # Só nothing_left chega aqui (buracos deixam < 0.5s); formato antigo.
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    if not swapped:
        # Só acontece com mais de um processo da API (o lock em memória já
        # barra o caso de um processo só).
        raise HTTPException(
            status_code=409,
            detail="Este clipe foi alterado por outra edição enquanto renderizava. Recarregue e tente de novo.",
        )
    return _clip_to_out(clip)
