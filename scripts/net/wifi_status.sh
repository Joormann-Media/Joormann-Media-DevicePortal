#!/usr/bin/env bash
set -euo pipefail

IFACE="${1:-wlan0}"
NMCLI="$(command -v nmcli || true)"
WPA_CLI="$(command -v wpa_cli || true)"
IW="$(command -v iw || true)"

if [[ -z "${NMCLI}" ]]; then
  echo "nmcli not found" >&2
  exit 127
fi

radio="$("${NMCLI}" -t -f WIFI general 2>/dev/null | head -n1 || true)"
state="$("${NMCLI}" -t -f GENERAL.STATE device show "${IFACE}" 2>/dev/null | sed -n 's/^GENERAL\.STATE://p' | head -n1)"
connection="$("${NMCLI}" -t -f GENERAL.CONNECTION device show "${IFACE}" 2>/dev/null | sed -n 's/^GENERAL\.CONNECTION://p' | head -n1)"
ip4="$(ip -4 -o addr show dev "${IFACE}" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1)"
ssid=""
bssid=""
signal=""
freq=""
security=""
wpa_state=""

wifi_line="$("${NMCLI}" -t -f ACTIVE,SSID,BSSID,SIGNAL,FREQ,SECURITY dev wifi list ifname "${IFACE}" 2>/dev/null | awk -F: '$1=="yes"||$1=="*" {print; exit}')"
if [[ -n "${wifi_line}" ]]; then
  ssid="$(echo "${wifi_line}" | cut -d: -f2)"
  bssid="$(echo "${wifi_line}" | cut -d: -f3)"
  signal="$(echo "${wifi_line}" | cut -d: -f4)"
  freq="$(echo "${wifi_line}" | cut -d: -f5)"
  security="$(echo "${wifi_line}" | cut -d: -f6-)"
fi

if [[ -n "${WPA_CLI}" ]]; then
  wpa_status="$("${WPA_CLI}" -i "${IFACE}" status 2>/dev/null || true)"
  wpa_state="$(echo "${wpa_status}" | sed -n 's/^wpa_state=//p' | head -n1)"
  if [[ -z "${ssid}" ]]; then
    ssid="$(echo "${wpa_status}" | sed -n 's/^ssid=//p' | head -n1)"
  fi
  if [[ -z "${bssid}" ]]; then
    bssid="$(echo "${wpa_status}" | sed -n 's/^bssid=//p' | head -n1)"
  fi
  if [[ -z "${freq}" ]]; then
    freq="$(echo "${wpa_status}" | sed -n 's/^freq=//p' | head -n1)"
  fi
fi

if [[ -z "${signal}" && -n "${IW}" ]]; then
  signal="$("${IW}" dev "${IFACE}" link 2>/dev/null | awk '/signal:/ {print int($2); exit}' || true)"
fi

connected="false"
if [[ "${state}" == 100* ]] || [[ "${wpa_state}" == "COMPLETED" ]]; then
  connected="true"
fi

echo "iface=${IFACE}"
echo "radio=${radio}"
echo "device_state=${state}"
echo "connection=${connection}"
echo "connected=${connected}"
echo "wpa_state=${wpa_state}"
echo "ssid=${ssid}"
echo "bssid=${bssid}"
echo "signal=${signal}"
echo "frequency_mhz=${freq}"
echo "security=${security}"
echo "ip=${ip4}"
