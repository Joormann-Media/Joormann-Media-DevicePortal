#!/usr/bin/env bash
set -euo pipefail

NEW_HOSTNAME_RAW="${1:-}"
AP_PROFILE="${2:-jm-hotspot}"
AP_CONF_FILE="/etc/joormann-media/provisioning/ap.conf"
BT_MAIN_CONF="/etc/bluetooth/main.conf"

if [[ -z "${NEW_HOSTNAME_RAW}" ]]; then
  echo "code=invalid_hostname"
  echo "message=Hostname fehlt"
  exit 2
fi

sanitize_hostname() {
  local value="$1"
  value="$(echo "$value" | tr '[:upper:]' '[:lower:]')"
  value="$(echo "$value" | sed -E 's/[[:space:]_]+/-/g; s/[^a-z0-9-]+/-/g; s/-+/-/g; s/^-+//; s/-+$//')"
  value="${value:0:63}"
  value="$(echo "$value" | sed -E 's/-+$//')"
  echo "$value"
}

NEW_HOSTNAME="$(sanitize_hostname "$NEW_HOSTNAME_RAW")"
if [[ -z "${NEW_HOSTNAME}" ]]; then
  echo "code=invalid_hostname"
  echo "message=Hostname ungültig"
  exit 2
fi

OLD_HOSTNAME="$(hostname 2>/dev/null || cat /etc/hostname 2>/dev/null || echo "")"
AP_SSID="${NEW_HOSTNAME}-ap"
AP_SSID="${AP_SSID:0:32}"
BT_NAME="${NEW_HOSTNAME}-bt"
BT_NAME="${BT_NAME:0:64}"

set_host_ok="0"
if command -v hostnamectl >/dev/null 2>&1; then
  if hostnamectl set-hostname "${NEW_HOSTNAME}" >/dev/null 2>&1; then
    set_host_ok="1"
  fi
fi

if [[ "$set_host_ok" != "1" ]]; then
  echo "${NEW_HOSTNAME}" > /etc/hostname
  if command -v hostname >/dev/null 2>&1; then
    hostname "${NEW_HOSTNAME}" >/dev/null 2>&1 || true
  fi
fi

if [[ -f "${AP_CONF_FILE}" ]]; then
  if grep -q '^SSID=' "${AP_CONF_FILE}"; then
    sed -i -E "s/^SSID=.*/SSID=${AP_SSID}/" "${AP_CONF_FILE}"
  else
    printf '\nSSID=%s\n' "${AP_SSID}" >> "${AP_CONF_FILE}"
  fi
fi

if command -v nmcli >/dev/null 2>&1; then
  if nmcli -t -f NAME connection show 2>/dev/null | grep -Fxq "${AP_PROFILE}"; then
    nmcli connection modify "${AP_PROFILE}" 802-11-wireless.ssid "${AP_SSID}" >/dev/null 2>&1 || true
  fi
fi

if [[ -f "${BT_MAIN_CONF}" ]]; then
  if grep -q '^Name=' "${BT_MAIN_CONF}"; then
    sed -i -E "s/^Name=.*/Name=${BT_NAME}/" "${BT_MAIN_CONF}"
  else
    printf '\nName=%s\n' "${BT_NAME}" >> "${BT_MAIN_CONF}"
  fi
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl try-restart bluetooth >/dev/null 2>&1 || true
fi

echo "success=true"
echo "old_hostname=${OLD_HOSTNAME}"
echo "new_hostname=${NEW_HOSTNAME}"
echo "ap_profile=${AP_PROFILE}"
echo "ap_ssid=${AP_SSID}"
echo "bt_name=${BT_NAME}"
echo "requires_reconnect=true"
echo "message=Hostname erfolgreich aktualisiert"
