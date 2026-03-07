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

extract_passkey_line() {
  echo "${log_dump}" | grep -Ei "passkey|pin code|pincode|request confirmation|confirm passkey|just-works" | tail -n1 || true
}

passkey_line="$(extract_passkey_line)"
passkey="$(echo "${passkey_line}" | grep -Eo '[0-9]{6}' | tail -n1 || true)"
passkey="${passkey:-}"

device_mac=""
device_name=""
paired_lines="$("${BTCTL}" paired-devices 2>/dev/null || true)"
if [[ -n "${paired_lines}" ]]; then
  last_paired="$(echo "${paired_lines}" | tail -n1)"
  device_mac="$(echo "${last_paired}" | awk '{print $2}' || true)"
  device_name="$(echo "${last_paired}" | cut -d' ' -f3- | xargs || true)"
fi

recent_line="$(echo "${log_dump}" | tail -n1 | xargs || true)"

echo "passkey=${passkey}"
echo "device_mac=${device_mac}"
echo "device_name=${device_name}"
echo "passkey_line=${passkey_line}"
echo "recent_line=${recent_line}"

