"""
Pipeline principal: recebe um job_id já criado (vídeo salvo em disco) e roda
todas as fases até gerar os cortes finais.

Fases (refletidas em Job.current_phase / Job.progress):
  [downloading ->] extracting_audio -> transcribing -> analyzing -> selecting -> cutting -> done

A fase "downloading" só roda quando o Video veio de um link (Video.source_url
preenchido, ver app/routers/upload.py::upload_from_url) em vez de upload de
arquivo — nesse caso video.filepath ainda está vazio quando a task começa.
"""
import json
import logging
import traceback
from pathlib import Path

from app.celery_app import celery_app
from app.config import settings
from app.database import SessionLocal
from app.models import Clip, Job, Video
from app.routers import watermark
from app.services import audio_analysis, face_detection, hashtags, video_cutter, video_downloader
from app.services.transcription import segments_overlapping, transcribe, write_srt

logger = logging.getLogger(__name__)


def _job_dirs(job_id: str) -> tuple[Path, Path, Path]:
    upload_dir = settings.upload_dir / job_id
    output_dir = settings.output_dir / job_id
    data_dir = settings.data_dir / job_id
    for d in (upload_dir, output_dir, data_dir):
        d.mkdir(parents=True, exist_ok=True)
    return upload_dir, output_dir, data_dir


@celery_app.task(name="pipeline.process_video", bind=True)
def process_video(self, job_id: str) -> None:
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            logger.error("Job %s não encontrado.", job_id)
            return
        video = db.get(Video, job.video_id)

        upload_dir, output_dir, data_dir = _job_dirs(job_id)

        def set_phase(phase: str, progress: int, message: str) -> None:
            job.status = "running"
            job.current_phase = phase
            job.progress = progress
            job.add_log(message)
            db.commit()

        # --- Fase 0 (opcional): download via link (YouTube/TikTok/Instagram) ---
        if video.source_url and (not video.filepath or not Path(video.filepath).exists()):
            set_phase("downloading", 1, f"Baixando vídeo de {video.source_url}...")

            def _on_download_progress(pct: float, _job=job) -> None:
                # Mapeia o 0-100% do download pra faixa 1-9% do progresso
                # geral do job (extracting_audio começa em 10%). Sem isso o
                # job ficava com um número fixo a fase de download inteira —
                # parece "travado" num vídeo grande/live longa, mesmo com o
                # download rodando normal (só dá pra ver via log do worker).
                new_progress = 1 + int(pct / 100 * 8)
                if new_progress != _job.progress:
                    _job.progress = new_progress
                    db.commit()

            # VideoDownloadError cai no except geral lá embaixo, que já marca
            # o job como failed com essa mensagem em error_message.
            downloaded_path, title = video_downloader.download_video(
                video.source_url, upload_dir, on_progress=_on_download_progress,
            )
            video.filepath = str(downloaded_path)
            if title:
                video.filename = title
            db.commit()
            job.add_log(f"Download concluído: {downloaded_path.name}" + (f" (\"{title}\")" if title else ""))
            db.commit()

        video_path = Path(video.filepath)

        # --- Fase 1: extração de áudio ---
        set_phase("extracting_audio", 10, "Extraindo áudio do vídeo...")
        audio_path = upload_dir / "audio.wav"
        video_cutter.extract_audio(video_path, audio_path)

        if video.duration is None:
            video.duration = video_cutter.probe_duration(video_path)
            db.commit()

        # --- Fase 2: transcrição ---
        set_phase("transcribing", 30, f"Transcrevendo áudio (Whisper '{settings.whisper_model_size}')...")
        transcription = transcribe(audio_path, language=job.language)
        job.language = job.language or transcription.language
        (data_dir / "transcript.json").write_text(
            json.dumps(
                {
                    "language": transcription.language,
                    "full_text": transcription.full_text,
                    "segments": [seg.__dict__ for seg in transcription.segments],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        full_srt_path = data_dir / "full.srt"
        write_srt(transcription.segments, full_srt_path)
        job.add_log(f"Transcrição concluída ({len(transcription.segments)} segmentos, idioma={transcription.language}).")
        db.commit()

        # --- Fase 3: análise / pontuação dos momentos ---
        set_phase("analyzing", 50, "Analisando picos de áudio e palavras-chave...")
        keywords = [k for k in (job.keywords or "").split(",") if k.strip()]
        windows = audio_analysis.score_windows(audio_path, transcription.segments, keywords)

        # --- Fase 4: seleção dos melhores momentos ---
        set_phase("selecting", 60, "Selecionando os melhores momentos...")
        candidates = audio_analysis.pick_top_clips(
            windows,
            max_clips=job.max_clips,
            clip_duration_min=job.clip_duration_min,
            clip_duration_max=job.clip_duration_max,
        )
        job.add_log(f"{len(candidates)} momento(s) selecionado(s).")

        # Estende o fim do corte até o fim da frase quando ele cai no meio de
        # um segmento de transcrição, evitando cortar a última palavra — desde
        # que não estoure clip_duration_max nem invada o próximo candidato.
        snapped = audio_analysis.snap_clip_ends_to_sentence(
            candidates,
            transcription.segments,
            clip_duration_max=job.clip_duration_max,
            total_duration=video.duration or 0.0,
        )
        candidates = []
        for candidate, extended_seconds in snapped:
            candidates.append(candidate)
            if extended_seconds > 0:
                job.add_log(
                    f"Corte estendido em {extended_seconds:.2f}s para não cortar no meio da frase "
                    f"(novo fim: {candidate.end:.2f}s)."
                )
        db.commit()

        # --- Fase 5: corte + legenda + hashtags para cada momento ---
        set_phase("cutting", 70, "Cortando e renderizando os clipes...")

        watermark_path = None
        if job.watermark_enabled:
            watermark_path = watermark.find_watermark_file()
            if watermark_path is None:
                job.add_log("Marca d'água habilitada mas nenhuma logo configurada (POST /watermark) — ignorando.")
                db.commit()

        # Tenta identificar a janela do streamer (webcam) uma vez só pro
        # vídeo inteiro — é best-effort (Haar cascade, sem modelo pesado) e
        # cai de volta pro crop central de sempre se não achar nada estável.
        # Ver app/services/face_detection.py pro motivo de cada heurística.
        streamer_region = None
        try:
            streamer_region = face_detection.detect_streamer_region(video_path, video.duration or 0.0)
        except Exception:  # noqa: BLE001 — detecção é um "extra", nunca deve derrubar o job
            logger.exception("Falha na detecção da janela do streamer (job %s) — seguindo sem ela.", job_id)
        # Persistido no Job (não só usado em memória aqui) pra que um re-corte
        # manual (POST /clips/{id}/recut) possa reaplicar o mesmo layout de
        # tela dividida em vez de reverter silenciosamente pro crop central.
        job.set_streamer_region(streamer_region)
        if streamer_region:
            job.add_log(f"Janela do streamer detectada em {streamer_region} — usando layout de tela dividida.")
        else:
            job.add_log("Nenhuma janela de streamer detectada — usando corte central padrão.")
        db.commit()

        for i, candidate in enumerate(candidates):
            # Só inclui um segmento se pelo menos 50% da duração dele estiver
            # de fato dentro do intervalo do corte — evita vazar texto de
            # frases que apenas tocam a borda do corte (transcript_text e .srt
            # ficam consistentes entre si). Ver services/transcription.py —
            # mesma função reaproveitada pelo re-corte manual (routers/clips.py).
            clip_segments = segments_overlapping(transcription.segments, candidate.start, candidate.end)
            clip_text = " ".join(seg.text for seg in clip_segments).strip()

            clip_srt_path = data_dir / f"clip_{i}.srt"
            write_srt(clip_segments, clip_srt_path, offset=candidate.start)

            output_path = output_dir / f"clip_{i}.mp4"
            video_cutter.cut_and_render_vertical(
                video_path, candidate.start, candidate.end, output_path,
                srt_path=clip_srt_path,
                watermark_path=watermark_path,
                watermark_position=job.watermark_position or "bottom-right",
                captions_enabled=job.captions_enabled if job.captions_enabled is not None else True,
                caption_font_size=job.caption_font_size or 8,
                caption_color=job.caption_color or "#FFFFFF",
                caption_style=job.caption_style or "bold_outline",
                caption_font_family=job.caption_font_family or "DejaVu Sans",
                streamer_region=streamer_region,
            )

            clip = Clip(
                video_id=video.id,
                job_id=job.id,
                index=i,
                start_time=candidate.start,
                end_time=candidate.end,
                score=candidate.score,
                processed_path=str(output_path),
                transcript_text=clip_text,
                hashtags=json.dumps(hashtags.generate_hashtags(clip_text), ensure_ascii=False),
            )
            db.add(clip)
            job.progress = 70 + int(20 * (i + 1) / max(len(candidates), 1))
            job.add_log(f"Corte {i + 1}/{len(candidates)} pronto: {output_path.name}")
            db.commit()

        # --- Fase 6: concluído ---
        job.status = "completed"
        job.current_phase = "done"
        job.progress = 100
        job.add_log("Processamento concluído.")
        video.status = "done"
        db.commit()

    except Exception as exc:  # noqa: BLE001 — precisa capturar tudo pra marcar o job como falho
        logger.exception("Falha no processamento do job %s", job_id)
        job = db.get(Job, job_id)
        if job is not None:
            job.status = "failed"
            job.error_message = f"{exc}\n\n{traceback.format_exc()[-4000:]}"
            job.add_log(f"ERRO: {exc}")
            video = db.get(Video, job.video_id)
            if video is not None:
                # Sem isso, Video.status ficava travado em "uploaded"/
                # "downloading" pra sempre num job que falhou — qualquer
                # código/ferramenta que inspecionasse Video diretamente
                # (em vez de Job) via um estado permanentemente inconsistente.
                video.status = "failed"
            db.commit()
        raise
    finally:
        db.close()
