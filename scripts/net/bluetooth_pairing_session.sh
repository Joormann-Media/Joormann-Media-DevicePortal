#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-}"
TIMEOUT="${2:-180}"

BTCTL="$(command -v bluetoothctl || true)"
if [[ -z "${BTCTL}" ]]; then
  echo "bluetoothctl not found" >&2
  exit 127
fi

RUNTIME_DIR="/run/deviceportal"
PID_FILE="${RUNTIME_DIR}/bt-pairing-agent.pid"
mkdir -p "${RUNTIME_DIR}"

is_running() {
  local pid="$1"
  [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null
}

read_pid() {
  if [[ -f "${PID_FILE}" ]]; then
    cat "${PID_FILE}" 2>/dev/null || true
  fi
}

cleanup_flags() {
  {
    echo "discoverable off"
    echo "pairable off"
    echo "quit"
  } | "${BTCTL}" >/dev/null 2>&1 || true
}

stop_session() {
  local pid
  pid="$(read_pid)"
  if is_running "${pid}"; then
    kill "${pid}" 2>/dev/null || true
    sleep 0.2
    if is_running "${pid}"; then
      kill -9 "${pid}" 2>/dev/null || true
    fi
  fi
  rm -f "${PID_FILE}"
  cleanup_flags
  echo "active=0"
}

start_session() {
  if [[ ! "${TIMEOUT}" =~ ^[0-9]+$ ]]; then
    echo "invalid timeout" >&2
    exit 2
  fi
  if [[ "${TIMEOUT}" -lt 30 ]]; then
    TIMEOUT=30
  fi
  if [[ "${TIMEOUT}" -gt 900 ]]; then
    TIMEOUT=900
  fi

  stop_session >/dev/null 2>&1 || true

  (
    {
      # Keep the agent alive for the full pairing window.
      echo "agent NoInputNoOutput"
      echo "default-agent"
      echo "power on"
      echo "discoverable-timeout ${TIMEOUT}"
      echo "pairable-timeout ${TIMEOUT}"
      echo "discoverable on"
      echo "pairable on"
      sleep "${TIMEOUT}"
      echo "discoverable off"
      echo "pairable off"
      echo "quit"
    } | "${BTCTL}" >/dev/null 2>&1
  ) &
  local pid=$!
  echo "${pid}" >"${PID_FILE}"

  echo "active=1"
  echo "pid=${pid}"
  echo "timeout=${TIMEOUT}"
}

status_session() {
  local pid
  pid="$(read_pid)"
  if is_running "${pid}"; then
    echo "active=1"
    echo "pid=${pid}"
  else
    rm -f "${PID_FILE}"
    echo "active=0"
  fi
}

case "${MODE}" in
  start)
    start_session
    ;;
  stop)
    stop_session
    ;;
  status)
    status_session
    ;;
  *)
    echo "usage: $0 {start [timeout]|stop|status}" >&2
    exit 2
    ;;
esac

