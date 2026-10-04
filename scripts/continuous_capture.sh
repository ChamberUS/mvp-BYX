#!/bin/bash
set -u

capture_retry_delay() {
  case "$1" in
    0|75) echo 10 ;;
    *) echo 300 ;;
  esac
}

if [ "${1:-}" = "--retry-delay" ]; then
  capture_retry_delay "${2:-2}"
  exit 0
fi

ROOT="/Users/buynnex-corp/dev/mvp-binance"
CLI="$ROOT/.venv/bin/adaptive-trader"

STATE="$HOME/.mvp-binance-capture"
LOGDIR="$STATE/logs"
PIDFILE="$STATE/capture.pid"
CURRENT="$STATE/current_campaign"

mkdir -p "$LOGDIR"

cd "$ROOT" || {
  echo "ERRO: nao foi possivel acessar $ROOT"
  exit 1
}

if [ ! -x "$CLI" ]; then
  echo "ERRO: adaptive-trader nao encontrado em:"
  echo "$CLI"
  exit 1
fi

echo $$ > "$PIDFILE"

# Mantem o macOS acordado enquanto este processo existir.
caffeinate -dimsu -w $$ >/dev/null 2>&1 &
CAFFEINATE_PID=$!

child_pid=""

cleanup() {
  trap - EXIT INT TERM

  echo
  echo "[$(date -u +%FT%TZ)] Encerrando captura..."

  if [ -n "${child_pid:-}" ]; then
    kill -TERM "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi

  kill "$CAFFEINATE_PID" 2>/dev/null || true
  rm -f "$PIDFILE"

  echo "Captura encerrada."
  exit 0
}

trap cleanup EXIT INT TERM

while true; do
  START_UTC="$(date -u +%Y%m%dT%H%M%SZ)"
  CAMPAIGN="ethusdt-futures-continuous-$START_UTC"

  echo "$CAMPAIGN" > "$CURRENT"

  LOG="$LOGDIR/$CAMPAIGN.log"

  {
    echo
    echo "============================================================"
    echo "[$(date -u +%FT%TZ)] INICIANDO CAMPANHA"
    echo "campaign_id: $CAMPAIGN"
    echo "market: USD-M Futures"
    echo "symbol: ETHUSDT"
    echo "chunk: 1800s"
    echo "target: 86400s"
    echo "============================================================"
  } | tee -a "$LOG"

  "$CLI" market microstructure campaign-record \
    --market futures \
    --symbol ETHUSDT \
    --campaign-id "$CAMPAIGN" \
    --streams aggTrade,bookTicker,depth,markPrice \
    --depth-speed 100ms \
    --chunk-seconds 1800 \
    --total-seconds 86400 \
    --output-dir data/microstructure \
    >> "$LOG" 2>&1 &

  child_pid=$!

  wait "$child_pid"
  RC=$?

  child_pid=""

  {
    echo
    echo "[$(date -u +%FT%TZ)] CAMPANHA FINALIZADA"
    echo "campaign_id: $CAMPAIGN"
    echo "exit_code: $RC"
  } | tee -a "$LOG"

  if [ "$RC" -eq 0 ]; then
    echo "Nova campanha sera iniciada em 10 segundos." | tee -a "$LOG"
  elif [ "$RC" -eq 75 ]; then
    echo "NO_SESSION/INCOMPLETE. Nova tentativa em 10 segundos." | tee -a "$LOG"
  else
    echo "Falha detectada. Nova tentativa em 5 minutos." | tee -a "$LOG"
  fi
  sleep "$(capture_retry_delay "$RC")"
done
