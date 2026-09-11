"""App Celery — worker que executa o pipeline de processamento de vídeo."""
from celery import Celery

from app.config import settings

celery_app = Celery(
    "videocutterai",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.pipeline", "app.tasks.relaxing_videos_pipeline"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Vídeo é pesado pra processar — evita que um worker pegue vários jobs
    # grandes ao mesmo tempo e estoure memória.
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    # IMPORTANTE: com broker Redis + task_acks_late=True, o Celery só marca a
    # task como "recebida" nesse período de visibilidade — se o processamento
    # (transcrição + corte) passar desse tempo, o Redis acha que o worker
    # morreu e REENTREGA o mesmo job pra outro worker, rodando o pipeline em
    # duplicidade (dois processos escrevendo Clips pro mesmo job_id ao mesmo
    # tempo). O default do Celery é 3600s (1h) — baixo demais pra VOD de live
    # de várias horas (transcrição com Whisper em CPU escala com a duração
    # do vídeo). Sobe pra 12h de folga.
    broker_transport_options={"visibility_timeout": 43200},
    # Sem task_time_limit/task_soft_time_limit de propósito: vídeo longo tem
    # que poder rodar o tempo que precisar, sem o worker matar a task no meio.
)
