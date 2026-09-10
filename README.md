# VideoCutterAI (criador-cortes)

Gerador automático de cortes verticais (9:16) para redes sociais a partir de
vídeo longo (podcast, gameplay, etc): transcreve com Whisper, detecta os
melhores momentos (picos de áudio + palavras-chave) e renderiza cortes com
legenda queimada, hashtags sugeridas por plataforma e tracking de métricas
de engajamento pós-publicação.

> Spec original veio de uma conversa com o DeepSeek — os arquivos
> `deepseek_*.md`/`.txt` na raiz ficam aqui como referência histórica e
> **não devem ser movidos/reorganizados**.

## Status atual — MVP backend

- ✅ Upload → transcrição (faster-whisper, CPU) → detecção de picos de
  áudio/palavras-chave → N cortes automáticos (default 3) → download.
- ✅ Hashtags sugeridas por corte, por plataforma (TikTok, Reels, Shorts,
  Facebook Reels).
- ✅ Tracking de métricas: lançamento manual (qualquer plataforma) + sync
  automático via YouTube Data API v3 (única que não exige app review).
- ✅ Telinha simples HTML + Chart.js (`/ui/metrics.html`) pra visualizar o
  histórico de métricas por clipe.
- ✅ Edição de um clipe já gerado (`POST /clips/{clip_id}/recut`): tempo
  (start/end) e/ou estilo de legenda, todos os campos opcionais — dá pra só
  reajustar o corte, só trocar o estilo, ou os dois juntos. Religar a
  legenda funciona (recalcula um `.srt` novo com offset a partir do
  transcript salvo), assim como desligá-la de vez.
- ✅ Frontend React + TypeScript (Vite, Tailwind dark-mode) cobrindo o fluxo
  upload → progresso (WebSocket) → clipes/hashtags/métricas/re-corte.
- ✅ Upload por link (`POST /upload-url`), além de arquivo: cola um link do
  YouTube/TikTok/Instagram (inclusive **VOD de live**, depois que ela
  termina) e o worker baixa via `yt-dlp` antes de processar. Testado de
  ponta a ponta com vídeos públicos reais do YouTube, incluindo um de ~19min
  pra validar escala (ver nota sobre vídeos longos abaixo). UI de "arquivo
  vs. link" já no frontend.
- ✅ Marca d'água/logo (`POST /watermark`, uma imagem global): liga/desliga e
  posiciona (4 cantos) por job, queimada no vídeo via overlay do ffmpeg.
- ✅ Legenda configurável: liga/desliga (opção "sem legenda"), tamanho de
  fonte, cor, 6 fontes (DejaVu Sans/Serif/Mono, Liberation Sans/Serif/Mono —
  estilo Arial/Times/Courier) e 4 presets de formato (negrito sem caixa /
  caixa preta clássica / minimalista / sem contorno) por job — e por clipe
  individual, via a edição pós-processamento acima.
- ✅ Editor visual por clipe (estilo CapCut, `ClipEditorModal.tsx`): timeline
  arrastável (início/fim) com prévia do vídeo ORIGINAL fazendo seek de
  verdade (`GET /jobs/{job_id}/original`, com suporte a `Range` de verdade —
  ver nota abaixo), + prévia ao vivo do estilo da legenda sobre o clipe já
  gerado (fonte/cor/formato mudam na hora, sem precisar re-renderizar pra
  ver como fica). "Aplicar" dispara o `/recut` de verdade no servidor.
- ✅ Tela "Meus cortes" (home): lista todos os jobs já processados (com
  status, data, contagem de clipes), vira a tela inicial do app.
- ✅ Detecção automática da janela do streamer (webcam) via OpenCV — quando
  encontra uma webcam estável num canto do vídeo, monta layout de tela
  dividida (streamer em cima, gameplay embaixo) em vez do crop central.
  Best-effort: se não achar nada confiável, cai pro crop central de sempre.
- ⏳ Fase 2 restante: zoom automático em quem fala, títulos/thumbnails
  sugeridos, sentimento via modelo de texto.

## Como rodar

```bash
cp .env.example .env
# edite YOUTUBE_API_KEY no .env se quiser o sync automático

docker compose up --build
```

- API: http://localhost:8000 (docs interativas em `/docs`)
- Telinha de métricas: http://localhost:8000/ui/metrics.html
- Frontend: http://localhost:5173 (via `docker compose up --build`, serviço
  `frontend`, nginx servindo o build estático)

### Frontend em dev (hot reload)

```bash
cd frontend
cp .env.example .env   # VITE_API_URL=http://localhost:8000 por padrão
npm install
npm run dev             # http://localhost:5173
```

Precisa do backend rodando (`docker compose up -d` na raiz, sem precisar
subir o serviço `frontend` do compose nesse caso).

## Endpoints principais

| Método | Rota | Descrição |
|---|---|---|
| POST | `/upload` | Envia o vídeo (multipart) + config de corte, cria o Job e dispara o pipeline |
| POST | `/upload-url` | Igual ao `/upload`, mas recebe um link (JSON `{url, ...}`) — o worker baixa via `yt-dlp` antes de processar |
| GET | `/status/{job_id}` | Status/progresso do Job (polling) |
| WS | `/ws/status/{job_id}` | Status em tempo real |
| GET | `/jobs/{job_id}/clips` | Lista os clipes gerados (com hashtags) |
| GET | `/clips/{clip_id}/file` | Baixa um clipe individual (mp4) |
| GET | `/jobs/{job_id}/download` | Baixa todos os clipes do job em ZIP |
| GET | `/jobs/{job_id}/original` | Serve o vídeo original com suporte a `Range` (usado pelo editor pra fazer seek sem baixar o arquivo inteiro) |
| POST | `/clips/{clip_id}/metrics` | Lança métrica manual (views/likes/comments/shares) |
| GET | `/clips/{clip_id}/metrics` | Histórico de métricas do clipe |
| POST | `/clips/{clip_id}/metrics/youtube-sync` | Puxa views/likes/comments atuais do YouTube |
| POST | `/clips/{clip_id}/recut` | Edita um clipe (tempo e/ou estilo de legenda — todos os campos opcionais), re-renderiza o mp4 |
| GET/POST/DELETE | `/watermark` | Consulta/define/remove a logo global (imagem única) |
| GET | `/watermark/file` | Serve a imagem da logo atual (preview) |
| GET | `/jobs` | Lista todos os jobs (resumo: status, data, vídeo, contagem de clipes) pra tela "Meus cortes" |
| DELETE | `/jobs/{job_id}` | Apaga o job (cascata: Video→Job→Clip→Metric) e os arquivos em disco |

## Revisão de código (2026-09-10)

Rodada uma revisão completa (backend+frontend) e corrigidos os achados reais:
- **Re-corte manual (`recut`) tinha 3 bugs:** sobrescrevia o mp4 processado
  direto (sem arquivo temporário — uma falha no meio do ffmpeg destruía o
  clipe bom anterior); nunca reaplicava o layout de tela dividida (streamer
  detectado); e não recalculava `transcript_text`/hashtags pro novo
  intervalo, deixando metadado do trecho antigo. Os 3 corrigidos: agora
  renderiza num temp file e só substitui após sucesso, a região do streamer
  é persistida em `Job.streamer_region` (nova coluna) e reaplicada, e
  texto/hashtags são recalculados a partir do transcript salvo em disco.
- SQLite sem WAL/busy_timeout entre o processo da API e o worker Celery
  (containers separados) podia gerar "database is locked" — corrigido em
  `database.py`.
- Sem validação server-side de `clip_duration_max > clip_duration_min` (só
  no formulário React) — corrigido nos dois endpoints de upload.
- `Video.status` nunca era atualizado quando um job falhava — corrigido.
- Hook de progresso do yt-dlp podia lançar `TypeError` e abortar um download
  longo — corrigido com um default seguro.
- `/jobs/{id}/download` reconstruía o zip do zero a cada clique — agora só
  reconstrói se algum clipe for mais novo que o zip existente, com troca
  atômica pra evitar zip corrompido em requests simultâneas.
- Lógica de "achar o arquivo da marca d'água" estava duplicada em 3 lugares
  — unificada num helper só (`find_watermark_file` em `routers/watermark.py`).

## Estrutura

```
backend/
  app/
    main.py            # FastAPI app
    config.py           # settings via .env
    database.py / models.py / schemas.py
    celery_app.py
    routers/             # upload, status (+ websocket), download, metrics, clips (recut)
    services/
      transcription.py   # faster-whisper
      audio_analysis.py  # picos de volume + palavras-chave -> pontuação -> seleção
      video_cutter.py     # ffmpeg: extrai áudio, corta, escala p/ 9:16, queima legenda
      hashtags.py         # hashtags por plataforma
      youtube_metrics.py  # sync via YouTube Data API v3
      video_downloader.py # baixa vídeo de link (YouTube/TikTok/Instagram) via yt-dlp
      face_detection.py   # detecta janela do streamer (webcam) via OpenCV/Haar cascade
    routers/watermark.py # logo global (upload/consulta/remoção)
    tasks/pipeline.py    # task Celery com o pipeline completo
  static/metrics.html    # telinha HTML+Chart.js de métricas
frontend/                  # React + TypeScript (Vite, Tailwind dark-mode)
  src/
    App.tsx                # estágios: home -> upload -> progresso -> clipes (sem router)
    api.ts                  # fetch nativo pro backend (VITE_API_URL)
    hooks/useJobStatus.ts   # WebSocket de status, cai pro polling se cair no meio do job
    components/             # HomeScreen, UploadForm, CaptionSection, WatermarkSection,
                             # ProgressPanel, ClipsList, ClipCard, ClipEditorModal,
                             # TimelineScrubber
  Dockerfile               # build com node, serve dist/ via nginx
docker-compose.yml         # redis + backend (API) + worker + frontend (nginx)
```

> Nota sobre `/clips/{clip_id}/recut`: campo omitido no request mantém o
> valor que o clipe/job já tinha (não reseta pro default do sistema) — dá
> pra mandar só `{"caption_style": "no_outline"}` pra só trocar o estilo,
> por exemplo, sem tocar no tempo do corte.

## Rodando sem Docker (dev)

Precisa de `ffmpeg` e `redis-server` instalados localmente.

```bash
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# terminal 1
uvicorn app.main:app --reload

# terminal 2
celery -A app.celery_app worker --loglevel=info --concurrency=1
```

## Próximos passos

1. Fase 2 restante: zoom automático em quem fala, títulos/thumbnails
   sugeridos, sentimento via modelo de texto.
2. Frontend: sem testes automatizados ainda (só verificação manual/`tsc`);
   considerar Vitest + Testing Library se o projeto crescer. CORS do backend
   está liberado geral (`allow_origins=["*"]`) — vale restringir ao domínio
   real do frontend antes de produção.
3. Marca d'água hoje é uma imagem só, global — se algum dia precisar de mais
   de uma logo (ex: marcas diferentes por conta), vira uma tabela em vez de
   um arquivo fixo em `data/watermark.*`. Limitação conhecida e não corrigida:
   a logo é resolvida por glob no início da fase de corte (não fixada na
   criação do job), então trocá-la enquanto um job antigo ainda está na fila
   pode fazer esse job usar a logo nova em vez da que estava ativa quando ele
   foi enviado — cenário raro (precisa trocar a logo bem no meio de um job
   rodando), documentado aqui em vez de corrigido.
4. Detecção de streamer é best-effort (Haar cascade, não modelo de rosto
   robusto) — funciona bem quando a webcam é nítida e fica num canto fixo,
   mas pode não achar nada em vídeos com câmera pequena/mal iluminada/webcam
   sem rosto claro; nesse caso cai pro crop central sem quebrar nada. Se a
   precisão incomodar no futuro, dá pra trocar o Haar cascade por um
   detector DNN (mais robusto, mas exige baixar um modelo maior).
5. Legenda em layout de tela dividida (streamer detectado) usa a mesma
   posição/MarginV do modo normal — funciona, mas a legenda pode ficar bem
   em cima do limite entre streamer/gameplay; um ajuste fino de posição
   específico pra esse layout é possível mas não foi feito ainda.

## Nota sobre `/upload-url` (yt-dlp)

Baixar vídeo de terceiros levanta questão de direitos autorais/ToS da
plataforma de origem — esse endpoint é pensado pra baixar **conteúdo próprio
do usuário** já publicado (ex: o podcast/live dele que já está no YouTube),
não pra baixar conteúdo alheio em massa. `yt-dlp` fica sem versão travada no
`requirements.txt` de propósito, porque as plataformas mudam a API/HTML
delas com frequência e uma versão antiga do yt-dlp quebra rápido.

## Nota sobre o editor visual e suporte a Range

O editor (`ClipEditorModal.tsx`) faz o `<video>` do vídeo ORIGINAL fazer seek
de verdade pra qualquer ponto (não só do trecho já cortado), o que só é
viável servindo o arquivo com suporte a `Range` — sem isso, cada busca no
player baixaria o vídeo inteiro (alguns GB). **Bug real encontrado
testando:** `FileResponse` do Starlette (até pelo menos 0.38.x, a versão
usada aqui) **não implementa Range nenhum** — sempre manda o arquivo
inteiro e ignora o header, mesmo respondendo como se tivesse dado certo.
Implementado manualmente em `_ranged_file_response` (`routers/download.py`)
por cima de `StreamingResponse`, com 206 Partial Content de verdade —
validado (`curl -H "Range: ..."`) buscando ~1MB no meio de um vídeo de 1GB
em 15ms, em vez de baixar o arquivo inteiro.

## Nota sobre detecção de streamer (webcam)

Roda uma vez por job (não por clipe), via `app/services/face_detection.py`:
extrai ~14 frames espalhados pelo vídeo (via `ffmpeg`, não `cv2.VideoCapture`
— ver motivo abaixo), roda um Haar cascade de rosto em cada um, e só aceita
como "achou a webcam" se um rosto aparecer perto de uma quina da tela em
mais amostras do que qualquer outro canto (não precisa maioria absoluta —
streamer real vira o rosto, sai de quadro etc. na maior parte do tempo).

Duas armadilhas reais encontradas testando com vídeo de verdade:
- **`cv2.VideoCapture` não decodifica direito vídeo AV1** (formato comum em
  downloads do yt-dlp) — ficava com frames corrompidos e a detecção falhava
  100% das vezes, mesmo com uma webcam bem nítida no vídeo. Por isso os
  frames são extraídos via `ffmpeg` (que já decodifica esses vídeos
  corretamente em outras partes do projeto) e só then passados pro OpenCV.
- **O minimap circular de jogos (testado com Red Dead Redemption 2) engana
  o Haar cascade de rosto** — é redondo e fica sempre no mesmo canto, então
  bate os mesmos critérios de "estabilidade" que uma webcam real. Resolvido
  subindo o `minNeighbors` do cascade de 5 pra 12 (testado empiricamente:
  elimina o falso positivo do minimap sem perder nenhuma detecção real da
  webcam nos mesmos frames).

## Nota sobre vídeos longos (VOD de live)

Funciona pelo mesmo `/upload-url` — cola o link da VOD depois que a live
termina. Pontos que importam pra conteúdo de horas:

- **Tempo de processamento escala com a duração**: num teste real (~19min de
  vídeo), a transcrição levou ~4.3min (fator ~0.22x tempo real) rodando
  Whisper "small" em CPU — uma live de 3h deve levar uns 40min só nessa fase.
- **Sem timeout artificial**: o worker Celery não tem `task_time_limit`, de
  propósito, pra não matar um processamento longo no meio.
- **Redelivery do Redis**: com `task_acks_late=True`, o Redis reentregaria
  (duplicaria!) qualquer job com mais de 1h de processamento por causa do
  `visibility_timeout` padrão (1h) — corrigido subindo pra 12h em
  `app/celery_app.py`.
- **WebSocket de progresso**: se a conexão cair no meio de um job longo
  (rede instável, aba suspensa etc.), o frontend agora cai pro polling em
  vez de travar a tela no último progresso visto (`useJobStatus.ts`).
