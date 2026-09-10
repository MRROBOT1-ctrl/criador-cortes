# PROMPT PARA O CLAUDE CODE

Quero desenvolver um sistema completo de criação automática de cortes de vídeo para redes sociais (Shorts/Reels/TikTok). 

## O QUE O SISTEMA DEVE FAZER:
- Receber upload de vídeo longo (podcast, gameplay, etc)
- Analisar automaticamente o vídeo (áudio + imagem)
- Identificar os melhores momentos baseado em emoção, palavras-chave e movimento
- Gerar cortes verticais (9:16) com legendas automáticas

## TECNOLOGIAS:
- Backend: FastAPI (Python)
- Workers: Celery + Redis
- Transcrição: Whisper (openai-whisper)
- Edição: FFmpeg + moviepy
- Frontend: React + TypeScript + Tailwind
- Banco: SQLite/PostgreSQL

## COMEÇA COM:
1. Estrutura de pastas do projeto
2. Dockerfile + docker-compose.yml
3. Modelos de banco de dados (SQLAlchemy)
4. Endpoints básicos do FastAPI: /upload, /status, /download
5. Worker inicial com Celery para processamento

## QUERO QUE O CÓDIGO SEJA:
- Organizado (Clean Architecture / MVC)
- Bem comentado (em português)
- Com logs detalhados
- Com tratamento de erros
- Com progresso via WebSocket

## O PRIMEIRO MVP DEVE ENTREGAR:
- Upload funcional
- Transcrição com Whisper
- Detecção simples de picos de áudio
- Geração de 3 cortes automáticos
- Download dos cortes prontos

## ESTRUTURA DE PASTAS SUGERIDA: