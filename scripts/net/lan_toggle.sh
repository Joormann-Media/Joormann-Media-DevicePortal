#!/usr/bin/env bash
set -euo pipefail

STATE="${1:-}"
IFACE="${2:-eth0}"

if [[ -z "$STATE" ]]; then
  echo "missing state (up/down)" >&2
  exit 2
fi

case "$STATE" in
  up|on|enable|enabled|true|1)
    TARGET="up"
    ;;
  down|off|disable|disabled|false|0)
    TARGET="down"
    ;;
  *)
    echo "invalid state: $STATE" >&2
    exit 2
    ;;
esac

case "$IFACE" in
  eth0)
    ;;
  *)
    echo "interface not allowed: $IFACE" >&2
    exit 2
    ;;
esac

IP_BIN="$(command -v ip || true)"
if [[ -z "$IP_BIN" ]]; then
  echo "ip command not found" >&2
  exit 127
fi

"$IP_BIN" link set dev "$IFACE" "$TARGET"
echo "lan.$IFACE=$TARGET"
