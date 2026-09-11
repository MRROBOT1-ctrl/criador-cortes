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
import uuid
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
from app.services.clip_rendering import remap_clip_segments, remap_pan_track, resolve_job_render_defaults, safe_rerender
from app.services.transcription import Segment, load_transcript_segments, segments_overlapping, write_srt

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

    video = db.get(Video, clip.video_id)
    if video is None or not video.filepath or not Path(video.filepath).exists():
        raise HTTPException(status_code=404, detail="Vídeo original não encontrado.")
    if not clip.processed_path:
        raise HTTPException(status_code=409, detail="Clipe ainda não possui um arquivo processado para sobrescrever.")

    job = db.get(Job, clip.job_id)
    defaults = resolve_job_render_defaults(job)

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

    if override is not None:
        # O override é armazenado em tempo relativo ao início do CLIPE
        # (pré-corte) — soma `clip.start_time` de volta só pra reaproveitar
        # `remap_clip_segments`, que espera tempos ABSOLUTOS no vídeo
        # original e já cuida de descontar o offset E remapear pelos cortes
        # internos numa passada só (mesma função usada por recut_clip/
        # clip_cuts.py, evita ter uma terceira forma de calcular isso).
        override_segments = [
            Segment(start=s["start"] + clip.start_time, end=s["end"] + clip.start_time, text=s["text"])
            for s in override
        ]
        srt_segments, clip_text = remap_clip_segments(override_segments, clip.start_time, cut_segments)
    else:
        # Override removido: volta a derivar a legenda da transcrição do
        # Whisper pro intervalo atual do clipe, igual ao recut normal.
        transcript_segments = load_transcript_segments(settings.data_dir / clip.job_id)
        clip_segments = (
            segments_overlapping(transcript_segments, clip.start_time, clip.end_time) if transcript_segments else []
        )
        srt_segments, clip_text = remap_clip_segments(clip_segments, clip.start_time, cut_segments)

    tmp_srt_path = settings.data_dir / clip.job_id / f"clip_{clip.index}_captions_{uuid.uuid4().hex[:8]}.srt"
    srt_path = None
    if defaults.captions_enabled and srt_segments:
        write_srt(srt_segments, tmp_srt_path, offset=0.0)
        srt_path = tmp_srt_path

    # Pan manual (se houver) também precisa do mesmo remapeamento pelos
    # cortes internos só pra ESSA renderização — o valor salvo continua
    # pré-corte (ver docstring de remap_pan_track).
    render_pan_track = remap_pan_track(clip.get_manual_pan_keyframes(), cut_segments)

    safe_rerender(
        Path(video.filepath),
        clip.start_time,
        clip.end_time,
        Path(clip.processed_path),
        srt_path=srt_path,
        cleanup_srt=True,
        watermark_path=defaults.watermark_path,
        watermark_position=defaults.watermark_position,
        captions_enabled=defaults.captions_enabled,
        caption_font_size=defaults.caption_font_size,
        caption_color=defaults.caption_color,
        caption_style=defaults.caption_style,
        caption_font_family=defaults.caption_font_family,
        streamer_region=defaults.streamer_region,
        # Reaproveita o pan manual já configurado nesse clipe (se houver) —
        # editar só a legenda não pode silenciosamente jogar fora um zoom
        # manual que o usuário já tinha ajustado (ver app/routers/pan.py).
        manual_pan_track=render_pan_track,
        keep_segments=cut_segments,
        # Nada aqui muda o intervalo do clipe nem os cortes internos — as
        # janelas mutadas pelo filtro de palavrões (se houver) continuam
        # válidas do jeito que estão, sem precisar remapear (ver clip_cuts.py
        # pro caso em que os cortes internos MUDAM).
        mute_ranges=clip.get_mute_ranges(),
        relaxing_video_path=defaults.relaxing_video_path,
    )

    clip.transcript_text = clip_text
    clip.hashtags, clip.suggested_titles = clip_rendering.compute_clip_metadata(clip_text)
    db.commit()
    db.refresh(clip)
    return _clip_to_out(clip)
