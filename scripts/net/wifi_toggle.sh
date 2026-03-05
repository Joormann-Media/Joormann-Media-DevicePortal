#!/usr/bin/env bash
set -euo pipefail

STATE="${1:-}"
IFACE="${2:-wlan0}"
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
RFKILL="$(command -v rfkill || true)"
IP_BIN="$(command -v ip || true)"
if [[ -z "$NMCLI" ]]; then
  echo "nmcli not found" >&2
  exit 127
fi

if [[ "$TARGET" == "on" ]]; then
  if [[ -n "$RFKILL" ]]; then
    "$RFKILL" unblock wifi >/dev/null 2>&1 || true
  fi
  "$NMCLI" radio wifi on
  "$NMCLI" device set "$IFACE" managed yes >/dev/null 2>&1 || true
  if [[ -n "$IP_BIN" ]]; then
    "$IP_BIN" link set "$IFACE" up >/dev/null 2>&1 || true
  fi

  # If a known connection profile exists for this iface, ask NM to activate it.
  CONN_NAME="$("$NMCLI" -t -f NAME,TYPE,DEVICE connection show | awk -F: -v dev="$IFACE" '$2=="802-11-wireless" && ($3==dev || $3=="--") {print $1; exit}')"
  if [[ -n "$CONN_NAME" ]]; then
    "$NMCLI" connection up "$CONN_NAME" >/dev/null 2>&1 || true
  fi
else
  "$NMCLI" radio wifi off
fi

WIFI_RADIO="$("$NMCLI" -t -f WIFI general 2>/dev/null | head -n1 || true)"
DEVICE_STATE="$("$NMCLI" -t -f GENERAL.STATE device show "$IFACE" 2>/dev/null | sed -n 's/^GENERAL\.STATE://p' | head -n1 || true)"
echo "wifi=$TARGET"
echo "iface=$IFACE"
echo "radio=${WIFI_RADIO:-unknown}"
echo "state=${DEVICE_STATE:---}"
