#!/usr/bin/env bash
set -euo pipefail

BTCTL="$(command -v bluetoothctl || true)"
if [[ -z "${BTCTL}" ]]; then
  echo "bluetoothctl not found" >&2
  exit 127
fi

sanitize_text() {
  sed -E 's/\x1B\[[0-9;]*[A-Za-z]//g' | tr -d '\r' | sed -E 's/[[:cntrl:]]//g'
}

paired_lines="$("${BTCTL}" paired-devices 2>/dev/null || true)"
paired_lines="$(printf '%s\n' "${paired_lines}" | sanitize_text)"

echo "${paired_lines}" | while IFS= read -r line; do
  line="$(echo "${line}" | xargs || true)"
  [[ -z "${line}" ]] && continue
  if [[ "${line}" =~ ^Device[[:space:]]+(([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})[[:space:]]*(.*)$ ]]; then
    mac="${BASH_REMATCH[1]}"
    name="${BASH_REMATCH[3]}"
    name="$(echo "${name}" | xargs || true)"
    echo "device=${mac}|${name}"
  fi
done
