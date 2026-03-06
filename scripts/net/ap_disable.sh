#!/usr/bin/env bash
set -euo pipefail

IFACE="${1:-wlan0}"
PROFILE="${2:-jm-hotspot}"
NMCLI="$(command -v nmcli || true)"

if [[ -z "${NMCLI}" ]]; then
  echo "nmcli not found" >&2
  exit 127
fi

if [[ ! -d "/sys/class/net/${IFACE}" ]]; then
  echo "interface missing: ${IFACE}" >&2
  exit 4
fi

"${NMCLI}" connection down "${PROFILE}" >/dev/null 2>&1 || true
"${NMCLI}" device disconnect "${IFACE}" >/dev/null 2>&1 || true

echo "ifname=${IFACE}"
echo "profile=${PROFILE}"
echo "enabled=false"
