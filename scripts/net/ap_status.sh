#!/usr/bin/env bash
set -euo pipefail

IFACE="${1:-wlan0}"
PROFILE="${2:-jm-hotspot}"
NMCLI="$(command -v nmcli || true)"
IW="$(command -v iw || true)"

if [[ -z "${NMCLI}" ]]; then
  echo "nmcli not found" >&2
  exit 127
fi

if [[ ! -d "/sys/class/net/${IFACE}" ]]; then
  echo "interface missing: ${IFACE}" >&2
  exit 4
fi

ACTIVE_CONN="$("${NMCLI}" -t -f GENERAL.CONNECTION device show "${IFACE}" 2>/dev/null | sed -n 's/^GENERAL\.CONNECTION://p' | head -n1)"
DEVICE_STATE="$("${NMCLI}" -t -f GENERAL.STATE device show "${IFACE}" 2>/dev/null | sed -n 's/^GENERAL\.STATE://p' | head -n1)"
RADIO="$("${NMCLI}" -t -f WIFI general 2>/dev/null | head -n1 | tr '[:upper:]' '[:lower:]')"
SSID="$("${NMCLI}" -g 802-11-wireless.ssid connection show "${PROFILE}" 2>/dev/null | head -n1)"
AP_IP="$(ip -4 -o addr show dev "${IFACE}" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1)"

ACTIVE="false"
if [[ -n "${ACTIVE_CONN}" && "${ACTIVE_CONN}" == "${PROFILE}" ]]; then
  ACTIVE="true"
fi

CLIENTS_COUNT="0"
if [[ "${ACTIVE}" == "true" && -n "${IW}" ]]; then
  CLIENTS_COUNT="$(iw dev "${IFACE}" station dump 2>/dev/null | awk '/^Station / {n+=1} END {print n+0}')"
fi

echo "ifname=${IFACE}"
echo "profile=${PROFILE}"
echo "active=${ACTIVE}"
echo "ssid=${SSID}"
echo "ip=${AP_IP}"
echo "clients_count=${CLIENTS_COUNT}"
echo "radio=${RADIO}"
echo "device_state=${DEVICE_STATE}"
echo "active_connection=${ACTIVE_CONN}"
