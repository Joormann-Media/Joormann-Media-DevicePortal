#!/usr/bin/env bash
set -euo pipefail

IFACE="${1:-wlan0}"
NMCLI="$(command -v nmcli || true)"
if [[ -z "$NMCLI" ]]; then
  echo "nmcli not found" >&2
  exit 127
fi

if "$NMCLI" dev wifi connect --wps-pbc ifname "$IFACE"; then
  echo "wps_started_ifname=$IFACE"
  exit 0
fi

"$NMCLI" dev wifi connect --wps-pbc
echo "wps_started_generic=true"
