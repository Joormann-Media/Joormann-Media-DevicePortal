#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_ROOT/runtime/logs"
PID_FILE="$LOG_DIR/deviceportal.pid"

stop_pid() {
  local pid="$1"
  if [[ -z "$pid" ]]; then
    return 0
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  kill "$pid" 2>/dev/null || true
  sleep 1
  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" 2>/dev/null || true
  fi
}

stopped_any=0

svc_pid="$(systemctl show -p MainPID --value device-portal.service 2>/dev/null || true)"
if [[ -n "${svc_pid:-}" && "$svc_pid" != "0" ]]; then
  cmdline="$(tr '\0' ' ' </proc/"$svc_pid"/cmdline 2>/dev/null || true)"
  if [[ "$cmdline" == *"$PROJECT_ROOT"* ]]; then
    systemctl stop device-portal.service >/dev/null 2>&1 || true
    stopped_any=1
  fi
fi

if [[ -f "$PID_FILE" ]]; then
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${pid:-}" ]]; then
    stop_pid "$pid"
    stopped_any=1
  fi
fi

while read -r pid; do
  [[ -n "$pid" ]] || continue
  stop_pid "$pid"
  stopped_any=1
done < <(pgrep -af "$PROJECT_ROOT/.venv/bin/python -m app.main" 2>/dev/null | awk '{print $1}')

rm -f "$PID_FILE"

if [[ "$stopped_any" -eq 1 ]]; then
  echo "Gestoppt: deviceportal"
else
  echo "Keine laufende Instanz gefunden: deviceportal"
fi
