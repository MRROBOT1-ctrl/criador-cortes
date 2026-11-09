#!/usr/bin/env bash
# Sobe Redis, API, worker Celery e frontend em modo dev, sem Docker.
# Uso: ./start.sh    (Ctrl+C encerra tudo que este script iniciou)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
LOG_DIR="${TMPDIR:-/tmp}/criador-cortes-logs"
READY_TIMEOUT_SECONDS=90

PIDS=()

fail() {
  echo "ERRO: $1" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "'$1' não encontrado. $2"
}

port_in_use() {
  (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
}

cleanup() {
  trap - EXIT INT TERM
  if ((${#PIDS[@]})); then
    echo
    echo "Encerrando serviços..."
    kill "${PIDS[@]}" 2>/dev/null
    wait "${PIDS[@]}" 2>/dev/null
  fi
  echo "Encerrado."
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

start_service() {
  local name="$1" dir="$2"
  shift 2
  (cd "$dir" && exec "$@") >"$LOG_DIR/$name.log" 2>&1 &
  PIDS+=("$!")
  echo "  $name iniciado (log: $LOG_DIR/$name.log)"
}

wait_for_url() {
  local name="$1" url="$2" deadline=$((SECONDS + READY_TIMEOUT_SECONDS))
  until curl -fs -o /dev/null -m 3 "$url"; do
    ((SECONDS < deadline)) || fail "$name não respondeu em ${READY_TIMEOUT_SECONDS}s. Veja $LOG_DIR/$name.log"
    sleep 1
  done
}

require_command ffmpeg "Instale com: sudo apt install ffmpeg"
require_command redis-server "Instale com: sudo apt install redis-server"
require_command redis-cli "Instale com: sudo apt install redis-server"
require_command curl "Instale com: sudo apt install curl"
require_command npm "Instale o Node.js."
[[ -x "$BACKEND/.venv/bin/uvicorn" ]] ||
  fail "backend/.venv incompleta. Veja a primeira instalação em 'Rodando sem Docker' no README."
[[ -x "$FRONTEND/node_modules/.bin/vite" ]] ||
  fail "Dependências do frontend ausentes. Rode: cd frontend && npm install"

for port in 8000 5173; do
  port_in_use "$port" && fail "A porta $port já está em uso. Encerre o que estiver nela e rode de novo."
done

mkdir -p "$LOG_DIR"
echo "Subindo serviços..."

if port_in_use 6379; then
  redis-cli -p 6379 ping 2>/dev/null | grep -q PONG || fail "A porta 6379 está em uso, mas não responde como Redis."
  echo "  redis já estava rodando, reaproveitando"
else
  start_service redis "$LOG_DIR" redis-server --port 6379 --bind 127.0.0.1 --dir "$LOG_DIR"
fi

start_service api "$BACKEND" .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
start_service celery "$BACKEND" .venv/bin/celery -A app.celery_app worker --loglevel=info --concurrency=1
start_service frontend "$FRONTEND" node_modules/.bin/vite

wait_for_url api http://localhost:8000/health
wait_for_url frontend http://localhost:5173/

cat <<EOF

Tudo no ar:
  Frontend  http://localhost:5173
  API       http://localhost:8000/docs
  Logs      $LOG_DIR

Ctrl+C para encerrar.
EOF

# Se um serviço cair sozinho, encerra os demais em vez de ficar meio no ar.
wait -n "${PIDS[@]}"
echo "Um dos serviços parou. Veja os logs em $LOG_DIR" >&2
exit 1
