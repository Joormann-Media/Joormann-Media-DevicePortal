#!/usr/bin/env bash
set -euo pipefail

REQUESTED_IFACE="${1:-}"
WAIT_SECONDS="${2:-120}"
TARGET_BSSID="${3:-}"
TARGET_SSID="${4:-}"
DEFAULT_IFACE="wlan0"
NMCLI="$(command -v nmcli || true)"
IW="$(command -v iw || true)"
WPA_CLI="$(command -v wpa_cli || true)"
DHCPCD_BIN="$(command -v dhcpcd || true)"
DHCLIENT_BIN="$(command -v dhclient || true)"
TIMEOUT_BIN="$(command -v timeout || true)"
WPS_AUTOCONNECT_PRIORITY="${WPS_AUTOCONNECT_PRIORITY:-900}"

emit_result() {
  local success="$1"
  local code="$2"
  local message="$3"
  local details="$4"
  local hint="$5"
  local iface="$6"
  echo "success=${success}"
  echo "code=${code}"
  echo "message=${message}"
  echo "details=${details}"
  echo "hint=${hint}"
  echo "iface=${iface}"
}

if [[ -z "${NMCLI}" ]]; then
  emit_result "false" "nmcli_missing" "Failed to start WPS" "nmcli command not found" "Installiere NetworkManager/nmcli." "${REQUESTED_IFACE:-$DEFAULT_IFACE}"
  exit 127
fi

if ! systemctl is-active --quiet NetworkManager; then
  emit_result "false" "networkmanager_inactive" "Failed to start WPS" "NetworkManager service is not active." "Starte NetworkManager: sudo systemctl start NetworkManager" "${REQUESTED_IFACE:-$DEFAULT_IFACE}"
  exit 3
fi

detect_iface() {
  local iface
  iface="$("${NMCLI}" -t -f DEVICE,TYPE,STATE dev status 2>/dev/null | awk -F: '$2=="wifi" && $1!="" {print $1; exit}')"
  if [[ -n "${iface}" ]]; then
    echo "${iface}"
    return 0
  fi
  if [[ -n "${IW}" ]]; then
    iface="$("${IW}" dev 2>/dev/null | awk '$1=="Interface"{print $2; exit}')"
    if [[ -n "${iface}" ]]; then
      echo "${iface}"
      return 0
    fi
  fi
  if [[ -n "${REQUESTED_IFACE}" ]]; then
    echo "${REQUESTED_IFACE}"
    return 0
  fi
  echo "${DEFAULT_IFACE}"
}

collect_wifi_info() {
  local ssid bssid signal security freq ip conn state
  ssid=""
  bssid=""
  signal=""
  security=""
  freq=""
  ip=""
  conn=""
  state=""

  if [[ -n "${NMCLI}" ]]; then
    conn="$("${NMCLI}" -t -f GENERAL.CONNECTION device show "${IFACE}" 2>/dev/null | sed -n 's/^GENERAL\.CONNECTION://p' | head -n1)"
    state="$("${NMCLI}" -t -f GENERAL.STATE device show "${IFACE}" 2>/dev/null | sed -n 's/^GENERAL\.STATE://p' | head -n1)"
    local wifi_line
    wifi_line="$("${NMCLI}" -t -f ACTIVE,SSID,BSSID,SIGNAL,FREQ,SECURITY dev wifi list ifname "${IFACE}" 2>/dev/null | awk -F: '$1=="yes"||$1=="*" {print; exit}')"
    if [[ -n "${wifi_line}" ]]; then
      ssid="$(echo "${wifi_line}" | cut -d: -f2)"
      bssid="$(echo "${wifi_line}" | cut -d: -f3)"
      signal="$(echo "${wifi_line}" | cut -d: -f4)"
      freq="$(echo "${wifi_line}" | cut -d: -f5)"
      security="$(echo "${wifi_line}" | cut -d: -f6-)"
    fi
  fi

  if [[ -z "${ssid}" && -n "${WPA_CLI}" ]]; then
    local wpa_status_line
    wpa_status_line="$("${WPA_CLI}" -i "${IFACE}" status 2>/dev/null || true)"
    if [[ -n "${wpa_status_line}" ]]; then
      ssid="$(echo "${wpa_status_line}" | sed -n 's/^ssid=//p' | head -n1)"
      bssid="${bssid:-$(echo "${wpa_status_line}" | sed -n 's/^bssid=//p' | head -n1)}"
      freq="${freq:-$(echo "${wpa_status_line}" | sed -n 's/^freq=//p' | head -n1)}"
      if [[ -z "${conn}" && -n "${ssid}" ]]; then
        conn="${ssid}"
      fi
      if [[ -z "${state}" ]]; then
        state="$(echo "${wpa_status_line}" | sed -n 's/^wpa_state=//p' | head -n1)"
      fi
    fi
  fi

  if [[ -z "${signal}" && -n "${IW}" ]]; then
    signal="$("${IW}" dev "${IFACE}" link 2>/dev/null | awk '/signal:/ {print int($2); exit}')"
  fi

  ip="$(ip -4 -o addr show dev "${IFACE}" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1)"

  echo "ssid=${ssid}"
  echo "bssid=${bssid}"
  echo "signal=${signal}"
  echo "security=${security}"
  echo "frequency_mhz=${freq}"
  echo "ip=${ip}"
  echo "connection=${conn}"
  echo "device_state=${state}"
}

is_connected() {
  local state conn
  state="$("${NMCLI}" -t -f GENERAL.STATE device show "${IFACE}" 2>/dev/null | sed -n 's/^GENERAL\.STATE://p' | head -n1)"
  conn="$("${NMCLI}" -t -f GENERAL.CONNECTION device show "${IFACE}" 2>/dev/null | sed -n 's/^GENERAL\.CONNECTION://p' | head -n1)"
  if [[ "${state}" == 100* ]] || [[ -n "${conn}" && "${conn}" != "--" ]]; then
    return 0
  fi
  if [[ -n "${WPA_CLI}" ]]; then
    local wpa_state ssid
    wpa_state="$("${WPA_CLI}" -i "${IFACE}" status 2>/dev/null | sed -n 's/^wpa_state=//p' | head -n1)"
    ssid="$("${WPA_CLI}" -i "${IFACE}" status 2>/dev/null | sed -n 's/^ssid=//p' | head -n1)"
    if [[ "${wpa_state}" == "COMPLETED" && -n "${ssid}" ]]; then
      return 0
    fi
  fi
  return 1
}

ensure_ipv4() {
  local ip_now
  ip_now="$(ip -4 -o addr show dev "${IFACE}" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1)"
  if [[ -n "${ip_now}" ]]; then
    return 0
  fi

  if [[ -n "${DHCPCD_BIN}" ]]; then
    if [[ -n "${TIMEOUT_BIN}" ]]; then
      "${TIMEOUT_BIN}" 20 "${DHCPCD_BIN}" -n "${IFACE}" >/dev/null 2>&1 || true
      ip_now="$(ip -4 -o addr show dev "${IFACE}" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1)"
      if [[ -z "${ip_now}" ]]; then
        "${TIMEOUT_BIN}" 25 "${DHCPCD_BIN}" "${IFACE}" >/dev/null 2>&1 || true
      fi
    else
      "${DHCPCD_BIN}" -n "${IFACE}" >/dev/null 2>&1 || true
    fi
    return 0
  fi

  if [[ -n "${DHCLIENT_BIN}" ]]; then
    if [[ -n "${TIMEOUT_BIN}" ]]; then
      "${TIMEOUT_BIN}" 20 "${DHCLIENT_BIN}" -1 "${IFACE}" >/dev/null 2>&1 || true
    else
      "${DHCLIENT_BIN}" -1 "${IFACE}" >/dev/null 2>&1 || true
    fi
  fi
}

persist_wps_profile() {
  local conn_name
  conn_name="$("${NMCLI}" -t -f GENERAL.CONNECTION device show "${IFACE}" 2>/dev/null | sed -n 's/^GENERAL\.CONNECTION://p' | head -n1)"
  if [[ -z "${conn_name}" || "${conn_name}" == "--" ]]; then
    conn_name="$("${NMCLI}" -t -f NAME,TYPE connection show --active 2>/dev/null | awk -F: '$2=="wifi" {print $1; exit}')"
  fi
  if [[ -z "${conn_name}" || "${conn_name}" == "--" ]]; then
    return 0
  fi

  "${NMCLI}" connection modify "${conn_name}" connection.autoconnect yes connection.autoconnect-priority "${WPS_AUTOCONNECT_PRIORITY}" >/dev/null 2>&1 || true
  "${NMCLI}" connection up "${conn_name}" >/dev/null 2>&1 || true

  if [[ -n "${WPA_CLI}" ]]; then
    "${WPA_CLI}" -i "${IFACE}" save_config >/dev/null 2>&1 || true
  fi
}

IFACE="$(detect_iface)"
if [[ ! -d "/sys/class/net/${IFACE}" ]]; then
  emit_result "false" "wifi_interface_missing" "Failed to start WPS" "No usable Wi-Fi interface found (checked: ${IFACE})." "Pruefe WLAN-Adapter und NetworkManager Device-Status." "${IFACE}"
  exit 4
fi

WIFI_RADIO_STATE="$("${NMCLI}" radio wifi 2>/dev/null | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
if [[ "${WIFI_RADIO_STATE}" == "disabled" || "${WIFI_RADIO_STATE}" == "off" ]]; then
  "${NMCLI}" radio wifi on >/dev/null 2>&1 || {
    emit_result "false" "wifi_radio_off" "Failed to start WPS" "Wi-Fi radio is off and could not be enabled." "Aktiviere WLAN am Geraet oder via nmcli radio wifi on." "${IFACE}"
    exit 5
  }
fi
"${NMCLI}" device set "${IFACE}" managed yes >/dev/null 2>&1 || true
ip link set "${IFACE}" up >/dev/null 2>&1 || true

attempt_details=()
wps_started_method=""

run_attempt() {
  local label="$1"
  shift
  local out
  set +e
  out="$("$@" 2>&1)"
  local rc=$?
  set -e
  out="${out//$'\n'/ }"
  if [[ ${rc} -eq 0 ]]; then
    wps_started_method="${label}"
    return 0
  fi
  attempt_details+=("${label} (rc=${rc}): ${out}")
  return 1
}

NMCLI_HELP="$("${NMCLI}" device wifi connect --help 2>&1 || true)"
if echo "${NMCLI_HELP}" | grep -q -- "--wps-pbc"; then
  if [[ -n "${TARGET_SSID}" ]]; then
    run_attempt "nmcli dev wifi connect ${TARGET_SSID} -- --wps-pbc ifname ${IFACE}" "${NMCLI}" dev wifi connect "${TARGET_SSID}" -- --wps-pbc ifname "${IFACE}" || true
    if [[ -z "${wps_started_method}" ]]; then
      run_attempt "nmcli dev wifi connect ${TARGET_SSID} --wps-pbc ifname ${IFACE}" "${NMCLI}" dev wifi connect "${TARGET_SSID}" --wps-pbc ifname "${IFACE}" || true
    fi
    if [[ -z "${wps_started_method}" ]]; then
      run_attempt "nmcli dev wifi connect ${TARGET_SSID} -- --wps-pbc" "${NMCLI}" dev wifi connect "${TARGET_SSID}" -- --wps-pbc || true
    fi
  else
    run_attempt "nmcli device wifi connect -- --wps-pbc ifname ${IFACE}" "${NMCLI}" device wifi connect -- --wps-pbc ifname "${IFACE}" || true
    if [[ -z "${wps_started_method}" ]]; then
      run_attempt "nmcli device wifi connect --wps-pbc ifname ${IFACE}" "${NMCLI}" device wifi connect --wps-pbc ifname "${IFACE}" || true
    fi
    if [[ -z "${wps_started_method}" ]]; then
      run_attempt "nmcli dev wifi connect -- --wps-pbc ifname ${IFACE}" "${NMCLI}" dev wifi connect -- --wps-pbc ifname "${IFACE}" || true
    fi
  fi
else
  attempt_details+=("nmcli reports no --wps-pbc support on this version")
fi

if [[ -z "${wps_started_method}" ]]; then
  if [[ -n "${WPA_CLI}" ]]; then
    if [[ -n "${TARGET_BSSID}" ]]; then
      run_attempt "wpa_cli -i ${IFACE} wps_pbc ${TARGET_BSSID}" "${WPA_CLI}" -i "${IFACE}" wps_pbc "${TARGET_BSSID}" || true
      if [[ -z "${wps_started_method}" ]]; then
        run_attempt "wpa_cli -i ${IFACE} wps_pbc" "${WPA_CLI}" -i "${IFACE}" wps_pbc || true
      fi
    else
      run_attempt "wpa_cli -i ${IFACE} wps_pbc" "${WPA_CLI}" -i "${IFACE}" wps_pbc || true
    fi
  else
    attempt_details+=("wpa_cli command not available")
  fi
fi

if [[ -n "${wps_started_method}" ]]; then
  if ! [[ "${WAIT_SECONDS}" =~ ^[0-9]+$ ]]; then
    WAIT_SECONDS="120"
  fi
  if [[ "${WAIT_SECONDS}" -eq 0 ]]; then
    # Non-blocking mode for HTTP endpoints: finalize in background.
    (
      max_wait=130
      elapsed_bg=0
      while [[ ${elapsed_bg} -le ${max_wait} ]]; do
        if is_connected; then
          ensure_ipv4
          persist_wps_profile
          exit 0
        fi
        sleep 3
        elapsed_bg=$((elapsed_bg + 3))
      done
      exit 0
    ) >/dev/null 2>&1 &
    emit_result "true" "wps_started" "WPS wurde gestartet. Bitte jetzt innerhalb von 2 Minuten am Router die WPS-Taste druecken." "${wps_started_method}" "Noch keine Verbindung erkannt. Je nach Router kann es 30-120 Sekunden dauern." "${IFACE}"
    collect_wifi_info
    exit 0
  fi
  elapsed=0
  while [[ ${elapsed} -le ${WAIT_SECONDS} ]]; do
    if is_connected; then
      ensure_ipv4
      persist_wps_profile
      emit_result "true" "connected" "WLAN erfolgreich per WPS verbunden." "${wps_started_method}" "Verbindung hergestellt. Netzwerkinformationen wurden aktualisiert." "${IFACE}"
      collect_wifi_info
      exit 0
    fi
    sleep 3
    elapsed=$((elapsed + 3))
  done

  emit_result "true" "wps_started" "WPS wurde gestartet. Bitte jetzt innerhalb von 2 Minuten am Router die WPS-Taste druecken." "${wps_started_method}" "Noch keine Verbindung erkannt. Je nach Router kann es 30-120 Sekunden dauern." "${IFACE}"
  collect_wifi_info
  exit 0
fi

DETAILS_COMBINED="$(printf '%s || ' "${attempt_details[@]}" | sed 's/ || $//')"
emit_result "false" "wps_failed" "Failed to start WPS" "${DETAILS_COMBINED}" "Pruefe, ob dein Router WPS-PBC unterstuetzt und druecke danach die WPS-Taste." "${IFACE}"
exit 6
