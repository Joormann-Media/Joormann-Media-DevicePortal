#!/usr/bin/env bash
set -euo pipefail

STATE="${1:-}"
if [[ -z "$STATE" ]]; then
  echo "missing state (on/off)" >&2
  exit 2
fi

case "$STATE" in
  on|true|1|enable|enabled)
    ACTION="unblock"
    OUT_STATE="on"
    ;;
  off|false|0|disable|disabled)
    ACTION="block"
    OUT_STATE="off"
    ;;
  *)
    echo "invalid state: $STATE" >&2
    exit 2
    ;;
esac

RFKILL="$(command -v rfkill || true)"
if [[ -z "$RFKILL" ]]; then
  echo "rfkill not found" >&2
  exit 127
fi

"$RFKILL" "$ACTION" bluetooth
echo "bluetooth=$OUT_STATE"
