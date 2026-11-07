"""
Edição manual do TEXTO da legenda queimada num clipe específico.

Hoje a legenda vem 100% da transcrição do Whisper (às vezes erra uma
palavra, sigla, nome próprio etc.) — este router deixa o usuário corrigir o
TEXTO de cada linha (não o tempo de cada uma, embora o intervalo de cada
linha também seja editável junto, já que vem no mesmo payload) sem precisar
reprocessar o vídeo inteiro.

Mesmo padrão de app/routers/pan.py: GET devolve o estado EFETIVO (override
se existir, senão derivado do Whisper pro intervalo atual do clipe) e PUT
valida + persiste + re-renderiza usando os helpers de
app/services/clip_rendering.py.
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Clip, Job, Video
from app.routers.download import _clip_to_out
from app.schemas import ClipOut
from app.services import clip_rendering
from app.services.clip_rendering import (
    clip_render_lock,
    commit_render,
    remap_pan_track,
    render_clip,
    resolve_clip_segments,
)
from app.services.transcription import load_transcript_segments, segments_overlapping

router = APIRouter(tags=["captions"])


class CaptionLine(BaseModel):
    start: float = Field(description="Segundos desde o INÍCIO DO CLIPE (0 = start_time do clipe).")
    end: float = Field(description="Segundos desde o INÍCIO DO CLIPE.")
    text: str


class CaptionLinesIn(BaseModel):
    # Lista vazia == remove o override e volta pra transcrição do Whisper.
    segments: list[CaptionLine] = Field(default_factory=list)


class CaptionLinesOut(BaseModel):
    lines: list[CaptionLine]
    is_manual: bool  # False = as linhas vieram da transcrição automática (sem override salvo)


def _validate_caption_lines(lines: list[CaptionLine], clip_duration: float) -> None:
    if not lines:
        return
    prev_end = None
    for line in lines:
        if not line.text.strip():
            raise HTTPException(status_code=400, detail="Uma linha de legenda não pode ter texto vazio.")
        if line.start < 0:
            raise HTTPException(status_code=400, detail=f"start inválido ({line.start}) — precisa ser >= 0.")
        if line.end <= line.start:
            raise HTTPException(status_code=400, detail="end precisa ser maior que start em cada linha.")
        if line.end > clip_duration + 0.01:
            raise HTTPException(
                status_code=400,
                detail=f"end ({line.end}) ultrapassa a duração do clipe ({clip_duration:.2f}s).",
            )
        if prev_end is not None and line.start < prev_end - 0.001:
            raise HTTPException(
                status_code=400,
                detail="As linhas precisam estar em ordem crescente de tempo e sem sobreposição.",
            )
        prev_end = line.end


@router.get("/clips/{clip_id}/captions", response_model=CaptionLinesOut)
def get_captions(clip_id: str, db: Session = Depends(get_db)):
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clipe não encontrado.")

    override = clip.get_caption_segments_override()
    if override is not None:
        lines = [CaptionLine(start=s["start"], end=s["end"], text=s["text"]) for s in override]
        return CaptionLinesOut(lines=lines, is_manual=True)

    # Sem override: deriva as linhas da transcrição do Whisper pro intervalo
    # ATUAL do clipe (mesmo critério de sempre — segments_overlapping), só
    # que devolvidas em tempo RELATIVO ao clipe (não ao vídeo original) pra
    # UI poder editar/mostrar mm:ss consistente com o player do corte.
    transcript_segments = load_transcript_segments(settings.data_dir / clip.job_id)
    clip_segments = (
        segments_overlapping(transcript_segments, clip.start_time, clip.end_time) if transcript_segments else []
    )
    lines = [
        CaptionLine(
            start=max(0.0, seg.start - clip.start_time),
            end=max(0.0, seg.end - clip.start_time),
            text=seg.text,
        )
        for seg in clip_segments
    ]
    return CaptionLinesOut(lines=lines, is_manual=False)


@router.put("/clips/{clip_id}/captions", response_model=ClipOut)
def set_captions(clip_id: str, payload: CaptionLinesIn, db: Session = Depends(get_db)):
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clipe não encontrado.")

    # Lock por clipe (contrato §0.5): segurado durante validação + render +
    # gravação, com o clipe relido logo depois de adquirido (outra
    # renderização pode ter acabado de gravar). Ocupado = 409
    # render_in_progress no envelope novo; os demais erros continuam no
    # formato antigo.
    with clip_render_lock(clip.id):
        db.refresh(clip)
        return _set_captions_locked(clip, payload, db)


def _set_captions_locked(clip: Clip, payload: CaptionLinesIn, db: Session) -> ClipOut:
    video = db.get(Video, clip.video_id)
    if video is None or not video.filepath or not Path(video.filepath).exists():
        raise HTTPException(status_code=404, detail="Vídeo original não encontrado.")
    if not clip.processed_path:
        raise HTTPException(status_code=409, detail="Clipe ainda não possui um arquivo processado para sobrescrever.")

    job = db.get(Job, clip.job_id)

    clip_duration = clip.end_time - clip.start_time
    _validate_caption_lines(payload.segments, clip_duration)

    # Lista vazia = remove o override (volta pro Whisper); senão persiste os
    # tempos/texto exatamente como vieram (já relativos ao clipe, mesmo
    # sistema de coordenadas PRÉ-corte de Clip.cut_segments — ver
    # app/routers/clip_cuts.py). Guardar sempre em coordenadas pré-corte
    # (nunca já remapeadas) é o mesmo motivo de Clip.manual_pan_keyframes
    # nunca ser sobrescrito remapeado: cortes internos podem mudar depois
    # (PUT /clips/{id}/cuts) sem invalidar a correção de texto já feita.
    override = [{"start": ln.start, "end": ln.end, "text": ln.text.strip()} for ln in payload.segments] or None
    clip.set_caption_segments_override(override)

    # Cortes internos (se houver) precisam ser respeitados aqui — sem isso,
    # editar só o texto da legenda reverteria silenciosamente um corte
    # interno já configurado (ver app/routers/clip_cuts.py).
    cut_segments = clip.get_cut_segments()

    # resolve_clip_segments já lê o override que acabou de ser salvo (ou sua
    # ausência) direto do `clip` — mesma lógica que estava duplicada aqui,
    # em pan.py e em clip_cuts.py.
    srt_segments, clip_text = resolve_clip_segments(clip, cut_segments)

    # Pan manual (se houver) também precisa do mesmo remapeamento pelos
    # cortes internos só pra ESSA renderização — o valor salvo continua
    # pré-corte (ver docstring de remap_pan_track).
    render_pan_track = remap_pan_track(clip.get_manual_pan_keyframes(), cut_segments)

    new_path = render_clip(
        clip,
        video,
        job,
        srt_segments=srt_segments,
        # Reaproveita o pan manual já configurado nesse clipe (se houver) —
        # editar só a legenda não pode silenciosamente jogar fora um zoom
        # manual que o usuário já tinha ajustado (ver app/routers/pan.py).
        manual_pan_track=render_pan_track,
        cut_segments=cut_segments,
        # Nada aqui muda o intervalo do clipe nem os cortes internos — as
        # janelas mutadas pelo filtro de palavrões (se houver) continuam
        # válidas do jeito que estão, sem precisar remapear (ver clip_cuts.py
        # pro caso em que os cortes internos MUDAM).
        mute_ranges=clip.get_mute_ranges(),
    )

    clip.transcript_text = clip_text
    clip.hashtags, clip.suggested_titles = clip_rendering.compute_clip_metadata(clip_text)
    # Arquivo novo `_r{n+1}.mp4` (render_clip não toca no atual) +
    # revision += 1 + commit; o antigo só é apagado depois do commit.
    commit_render(db, clip, new_path)
    db.refresh(clip)
    return _clip_to_out(clip)
