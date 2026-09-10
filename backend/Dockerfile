FROM python:3.11-slim

# FFmpeg é usado tanto para extrair áudio quanto para cortar/renderizar os vídeos finais.
# fonts-dejavu-core + fonts-liberation dão as opções de fonte da legenda
# (DejaVu Sans/Serif/Mono + Liberation Sans/Serif/Mono, estilo Arial/Times/
# Courier). IMPORTANTE: `fonts-dejavu-core` estava presente nas imagens
# anteriores só como dependência incidental de outro pacote (nunca foi
# pedido explicitamente) — ao adicionar fonts-liberation, a resolução de
# dependências do apt deixou de puxá-lo, e o DejaVu sumiu da imagem sem
# aviso nenhum (bug real encontrado testando). Agora os dois vêm pedidos
# explicitamente, não dependendo de acaso de dependência transitiva.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static

# Pastas usadas em tempo de execução (também são montadas como volumes no docker-compose,
# mas criamos aqui para o caso de rodar a imagem isolada, sem compose).
RUN mkdir -p uploads outputs data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
