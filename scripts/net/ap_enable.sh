#!/usr/bin/env bash
set -euo pipefail

IFACE="${1:-wlan0}"
PROFILE="${2:-jm-hotspot}"
NMCLI="$(command -v nmcli || true)"
CONF_FILE="/etc/joormann-media/provisioning/ap.conf"

if [[ -z "${NMCLI}" ]]; then
  echo "nmcli not found" >&2
  exit 127
fi

if [[ ! -d "/sys/class/net/${IFACE}" ]]; then
  echo "interface missing: ${IFACE}" >&2
  exit 4
fi

# Build hotspot profile on demand (needed on devices that were not provisioned with AP profile yet).
if ! "${NMCLI}" -t -f NAME connection show 2>/dev/null | grep -Fxq "${PROFILE}"; then
  SSID="$(hostname 2>/dev/null || echo "device")-ap"
  OPEN="1"
  PASSWORD=""
  BAND="bg"
  CHANNEL="6"
  IP_CIDR="10.42.0.1/24"

  if [[ -f "${CONF_FILE}" ]]; then
    while IFS='=' read -r key value; do
      [[ -z "${key}" ]] && continue
      case "${key}" in
        \#*) continue ;;
        SSID) SSID="${value}" ;;
        OPEN) OPEN="${value}" ;;
        PASSWORD) PASSWORD="${value}" ;;
        BAND) BAND="${value}" ;;
        CHANNEL) CHANNEL="${value}" ;;
        IP_CIDR) IP_CIDR="${value}" ;;
      esac
    done < "${CONF_FILE}"
  fi

  SSID="$(echo "${SSID}" | sed 's/^ *//;s/ *$//')"
  OPEN="$(echo "${OPEN}" | tr '[:upper:]' '[:lower:]' | tr -d ' ')"
  PASSWORD="$(echo "${PASSWORD}" | sed 's/^ *//;s/ *$//')"
  BAND="$(echo "${BAND}" | tr '[:upper:]' '[:lower:]' | tr -d ' ')"
  CHANNEL="$(echo "${CHANNEL}" | tr -d ' ')"
  IP_CIDR="$(echo "${IP_CIDR}" | tr -d ' ')"

  [[ -z "${SSID}" ]] && SSID="device-ap"
  [[ "${BAND}" != "bg" && "${BAND}" != "a" ]] && BAND="bg"
  [[ -z "${CHANNEL}" ]] && CHANNEL="6"
  [[ -z "${IP_CIDR}" ]] && IP_CIDR="10.42.0.1/24"

  "${NMCLI}" connection add type wifi ifname "${IFACE}" con-name "${PROFILE}" autoconnect no ssid "${SSID}" >/dev/null
  "${NMCLI}" connection modify "${PROFILE}" connection.autoconnect no >/dev/null
  "${NMCLI}" connection modify "${PROFILE}" 802-11-wireless.mode ap 802-11-wireless.band "${BAND}" 802-11-wireless.channel "${CHANNEL}" >/dev/null
  "${NMCLI}" connection modify "${PROFILE}" ipv4.addresses "${IP_CIDR}" ipv4.method shared ipv6.method ignore >/dev/null

  if [[ "${OPEN}" == "1" || "${OPEN}" == "true" || "${OPEN}" == "yes" ]]; then
    "${NMCLI}" connection modify "${PROFILE}" -802-11-wireless-security >/dev/null 2>&1 || true
    "${NMCLI}" connection modify "${PROFILE}" wifi-sec.key-mgmt "" >/dev/null 2>&1 || true
  elif [[ ${#PASSWORD} -ge 8 ]]; then
    "${NMCLI}" connection modify "${PROFILE}" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "${PASSWORD}" >/dev/null
  else
    "${NMCLI}" connection modify "${PROFILE}" -802-11-wireless-security >/dev/null 2>&1 || true
    "${NMCLI}" connection modify "${PROFILE}" wifi-sec.key-mgmt "" >/dev/null 2>&1 || true
  fi
fi

"${NMCLI}" radio wifi on >/dev/null 2>&1 || true
"${NMCLI}" device set "${IFACE}" managed yes >/dev/null 2>&1 || true
"${NMCLI}" connection up "${PROFILE}" ifname "${IFACE}" >/dev/null

echo "ifname=${IFACE}"
echo "profile=${PROFILE}"
echo "enabled=true"
