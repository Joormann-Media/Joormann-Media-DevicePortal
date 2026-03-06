#!/usr/bin/env bash
set -euo pipefail

IFACE="${1:-wlan0}"
NMCLI="$(command -v nmcli || true)"
WPA_CLI="$(command -v wpa_cli || true)"

if [[ -z "${NMCLI}" ]]; then
  echo "nmcli not found" >&2
  exit 127
fi

set +e
OUT="$("${NMCLI}" device disconnect "${IFACE}" 2>&1)"
RC=$?
set -e
if [[ ${RC} -ne 0 ]]; then
  if [[ -n "${WPA_CLI}" ]]; then
    "${WPA_CLI}" -i "${IFACE}" disconnect >/dev/null 2>&1 || true
  fi
fi

echo "iface=${IFACE}"
echo "rc=${RC}"
echo "output=${OUT//$'\n'/ }"
