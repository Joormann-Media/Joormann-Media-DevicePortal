#!/usr/bin/env bash
set -euo pipefail

IFACE="${1:-wlan0}"
DHCPCD_BIN="$(command -v dhcpcd || true)"
DHCLIENT_BIN="$(command -v dhclient || true)"
TIMEOUT_BIN="$(command -v timeout || true)"

if [[ -n "${DHCPCD_BIN}" ]]; then
  if [[ -n "${TIMEOUT_BIN}" ]]; then
    "${TIMEOUT_BIN}" 20 "${DHCPCD_BIN}" -n "${IFACE}" >/dev/null 2>&1 || true
    "${TIMEOUT_BIN}" 25 "${DHCPCD_BIN}" "${IFACE}" >/dev/null 2>&1 || true
  else
    "${DHCPCD_BIN}" -n "${IFACE}" >/dev/null 2>&1 || true
  fi
elif [[ -n "${DHCLIENT_BIN}" ]]; then
  if [[ -n "${TIMEOUT_BIN}" ]]; then
    "${TIMEOUT_BIN}" 20 "${DHCLIENT_BIN}" -1 "${IFACE}" >/dev/null 2>&1 || true
  else
    "${DHCLIENT_BIN}" -1 "${IFACE}" >/dev/null 2>&1 || true
  fi
else
  echo "no_dhcp_client"
  exit 127
fi

IP4="$(ip -4 -o addr show dev "${IFACE}" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1)"
echo "iface=${IFACE}"
echo "ip=${IP4}"
