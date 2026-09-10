## BACKEND

**Linguagem:** Python 3.11+
**Framework:** FastAPI (assíncrono, alta performance)
**Processamento:** Celery + Redis (para tarefas longas)
**Armazenamento:** 
  - Local: uploads/ e outputs/
  - Opcional: S3 / Cloud Storage

**Principais Bibliotecas:**
- moviepy / ffmpeg-python → Edição de vídeo
- openai-whisper → Transcrição automática
- torch + transformers → Análise de sentimento/emoção
- yt-dlp → Download de vídeos (opcional)
- faster-whisper → Versão otimizada do Whisper
- pydub → Manipulação de áudio
- opencv-python → Detecção de movimento/faces
- numpy + scipy → Processamento de sinais

**APIs Externas (opcional, para melhorar):**
- OpenAI API → Para análise de sentimento avançada
- Deepgram / AssemblyAI → Transcrição alternativa
- Replicate / HuggingFace → Modelos de IA sob demanda

## FRONTEND

**Framework:** React + TypeScript
**Estilização:** TailwindCSS + ShadcnUI
**Upload:** Dropzone com upload chunked
**Player:** Video.js ou Plyr
**Comunicação:** 
  - REST API para ações rápidas
  - WebSockets para progresso em tempo real
  - SSE (Server-Sent Events) para status de processamento

**Estado Global:** Zustand ou Context API

## BANCO DE DADOS

**SQLite** (desenvolvimento) / **PostgreSQL** (produção)

**Modelos principais:**
- Video: id, filename, filepath, duration, status, created_at
- Clip: id, video_id, start_time, end_time, score, processed_path
- Job: id, video_id, status, progress, logs, created_at
- User: id, name, email (futuro)
- Settings: user_id, clip_duration, language, min_confidence

## INFRAESTRUTURA

- Docker + Docker Compose
- Redis para filas e cache
- Nginx para servir arquivos estáticos
- GPU recomendada para processamento mais rápido
- Suporte a CPU (mais lento mas funcional)