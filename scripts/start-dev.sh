#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_ROOT/runtime/logs"
PID_FILE="$LOG_DIR/deviceportal.pid"
LOG_FILE="$LOG_DIR/deviceportal.log"

CONFIG_DIR="$PROJECT_ROOT/config"
PORTS_ENV_FILE="${JARVIS_PORTS_FILE:-$CONFIG_DIR/ports.env}"
PORTS_LOCAL_FILE="${JARVIS_PORTS_LOCAL_FILE:-$CONFIG_DIR/ports.local.env}"

if [[ -f "$PORTS_ENV_FILE" ]]; then
  set -a
  source "$PORTS_ENV_FILE"
  set +a
fi
if [[ -f "$PORTS_LOCAL_FILE" ]]; then
  set -a
  source "$PORTS_LOCAL_FILE"
  set +a
fi

PORTAL_HOST="${PORTAL_HOST:-0.0.0.0}"
PORTAL_PORT="${PORTAL_PORT:-5070}"
FLASK_DEBUG="${FLASK_DEBUG:-0}"
AUTO_PORT_FALLBACK="${AUTO_PORT_FALLBACK:-1}"

is_port_in_use() {
  local port="$1"
  python3 - "$port" <<'PY' >/dev/null 2>&1
import socket, sys
port = int(sys.argv[1])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(0.5)
    sys.exit(0 if sock.connect_ex(("127.0.0.1", port)) == 0 else 1)
PY
}

find_next_free_port() {
  local port="$1"
  local tries=0
  while is_port_in_use "$port"; do
    port=$((port + 1))
    tries=$((tries + 1))
    if [ "$tries" -ge 200 ]; then
      echo ""
      return 1
    fi
  done
  echo "$port"
}

persist_ports_local() {
  mkdir -p "$CONFIG_DIR"
  cat > "$PORTS_LOCAL_FILE" <<EOF
PORTAL_HOST=$PORTAL_HOST
PORTAL_PORT=$PORTAL_PORT
FLASK_DEBUG=$FLASK_DEBUG
EOF
}

mkdir -p "$LOG_DIR"

VENV_DIR="$PROJECT_ROOT/.venv"
PYTHON_BIN="$VENV_DIR/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Erstelle virtuelle Umgebung: $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"

if [[ -f "$PROJECT_ROOT/requirements.txt" ]]; then
  echo "Installiere/aktualisiere Requirements ..."
  "$PYTHON_BIN" -m pip install -q --upgrade pip
  "$PYTHON_BIN" -m pip install -q -r "$PROJECT_ROOT/requirements.txt"
fi

if [[ -f "$PID_FILE" ]]; then
  existing_pid="$(cat "$PID_FILE")"
  if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" 2>/dev/null; then
    echo "Bereits aktiv: deviceportal (PID $existing_pid)"
    echo "URL: http://${PORTAL_HOST}:${PORTAL_PORT}"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

if is_port_in_use "$PORTAL_PORT"; then
  if [ "$AUTO_PORT_FALLBACK" = "1" ] || [ "$AUTO_PORT_FALLBACK" = "true" ] || [ "$AUTO_PORT_FALLBACK" = "yes" ]; then
    next_port="$(find_next_free_port "$PORTAL_PORT")"
    if [ -z "$next_port" ]; then
      echo "Port bereits belegt: ${PORTAL_PORT}. Kein freier Fallback-Port gefunden."
      exit 1
    fi
    echo "Port bereits belegt: ${PORTAL_PORT}. Wechsle auf freien Port: ${next_port}"
    PORTAL_PORT="$next_port"
    persist_ports_local
  else
    echo "Port bereits belegt: ${PORTAL_PORT}. Start abgebrochen."
    exit 1
  fi
fi

(
  cd "$PROJECT_ROOT"
  nohup env PORTAL_HOST="$PORTAL_HOST" PORTAL_PORT="$PORTAL_PORT" FLASK_DEBUG="$FLASK_DEBUG" \
    PYTHONUNBUFFERED=1 \
    "$PYTHON_BIN" app/main.py >>"$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"
)

sleep 1
pid="$(cat "$PID_FILE")"
if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
  echo "Gestartet: deviceportal (PID $pid)"
  echo "URL: http://${PORTAL_HOST}:${PORTAL_PORT}"
  echo "Log: $LOG_FILE"
else
  rm -f "$PID_FILE"
  echo "Fehlgeschlagen. Siehe Log: $LOG_FILE"
  exit 1
fi
