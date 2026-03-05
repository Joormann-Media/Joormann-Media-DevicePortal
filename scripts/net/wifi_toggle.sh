#!/usr/bin/env bash
set -euo pipefail

STATE="${1:-}"
if [[ -z "$STATE" ]]; then
  echo "missing state (on/off)" >&2
  exit 2
fi

case "$STATE" in
  on|true|1|enable|enabled)
    TARGET="on"
    ;;
  off|false|0|disable|disabled)
    TARGET="off"
    ;;
  *)
    echo "invalid state: $STATE" >&2
    exit 2
    ;;
esac

NMCLI="$(command -v nmcli || true)"
if [[ -z "$NMCLI" ]]; then
  echo "nmcli not found" >&2
  exit 127
fi

"$NMCLI" radio wifi "$TARGET"
echo "wifi=$TARGET"
