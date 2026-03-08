#!/usr/bin/env bash
set -euo pipefail

WINDOW_SEC="${1:-300}"
if [[ ! "${WINDOW_SEC}" =~ ^[0-9]+$ ]]; then
  echo "invalid window seconds" >&2
  exit 2
fi

BTCTL="$(command -v bluetoothctl || true)"
if [[ -z "${BTCTL}" ]]; then
  echo "bluetoothctl not found" >&2
  exit 127
fi

JOURNALCTL="$(command -v journalctl || true)"
RUNTIME_LOG="/run/deviceportal/bt-pairing-agent.log"

sanitize_text() {
  sed -E 's/\x1B\[[0-9;]*[A-Za-z]//g' | tr -d '\r' | sed -E 's/[[:cntrl:]]//g'
}

now_epoch="$(date +%s)"
since_epoch="$(( now_epoch - WINDOW_SEC ))"
if [[ "${since_epoch}" -lt 0 ]]; then
  since_epoch=0
fi
since_human="$(date -u -d "@${since_epoch}" '+%Y-%m-%d %H:%M:%S')"

log_dump=""
if [[ -n "${JOURNALCTL}" ]]; then
  log_dump="$("${JOURNALCTL}" -u bluetooth --since "${since_human}" --no-pager -n 400 -o cat 2>/dev/null || true)"
fi
if [[ -f "${RUNTIME_LOG}" ]]; then
  runtime_tail="$(tail -n 600 "${RUNTIME_LOG}" 2>/dev/null || true)"
  if [[ -n "${runtime_tail}" ]]; then
    log_dump="${log_dump}"$'\n'"${runtime_tail}"
  fi
fi
log_dump="$(printf '%s\n' "${log_dump}" | sanitize_text)"

extract_passkey_line() {
  echo "${log_dump}" | grep -Ei "passkey|pin code|pincode|request confirmation|confirm passkey|just-works|confirm value|authorize service|agent.*confirm" | tail -n1 || true
}

passkey_line="$(extract_passkey_line)"
passkey="$(echo "${passkey_line}" | grep -Eo '[0-9]{6}' | tail -n1 || true)"
passkey="${passkey:-}"
pending_mac="$(echo "${passkey_line}" | grep -Eo '([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}' | tail -n1 || true)"
pending_mac="${pending_mac:-}"

device_mac=""
device_name=""
paired_lines="$("${BTCTL}" paired-devices 2>/dev/null || true)"
if [[ -n "${paired_lines}" ]]; then
  paired_lines="$(printf '%s\n' "${paired_lines}" | sanitize_text)"
  last_paired="$(echo "${paired_lines}" | grep -E '^Device ([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2} ' | tail -n1 || true)"
  device_mac="$(echo "${last_paired}" | awk '{print $2}' || true)"
  device_name="$(echo "${last_paired}" | cut -d' ' -f3- | xargs || true)"
fi

recent_line="$(echo "${log_dump}" | tail -n1 | xargs || true)"
if [[ -z "${pending_mac}" ]]; then
  pending_mac="$(echo "${recent_line}" | grep -Eo '([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}' | tail -n1 || true)"
  pending_mac="${pending_mac:-}"
fi
if [[ -z "${pending_mac}" ]]; then
  pending_mac="$(echo "${log_dump}" | grep -Eo '([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}' | tail -n1 || true)"
  pending_mac="${pending_mac:-}"
fi

echo "passkey=${passkey}"
echo "pending_mac=${pending_mac}"
echo "device_mac=${device_mac}"
echo "device_name=${device_name}"
echo "passkey_line=${passkey_line}"
echo "recent_line=${recent_line}"
