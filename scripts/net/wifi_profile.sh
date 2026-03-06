#!/usr/bin/env bash
set -euo pipefail

CMD="${1:-}"
shift || true

NMCLI="$(command -v nmcli || true)"
DEFAULT_IFACE="wlan0"

if [[ -z "${NMCLI}" ]]; then
  echo "nmcli not found" >&2
  exit 127
fi

detect_iface() {
  local iface
  iface="$("${NMCLI}" -t -f DEVICE,TYPE dev status 2>/dev/null | awk -F: '$2=="wifi" && $1!="" {print $1; exit}')"
  if [[ -n "${iface}" ]]; then
    echo "${iface}"
    return 0
  fi
  echo "${DEFAULT_IFACE}"
}

run_nmcli() {
  set +e
  local out
  out="$("${NMCLI}" "$@" 2>&1)"
  local rc=$?
  set -e
  if [[ ${rc} -ne 0 ]]; then
    echo "${out}" >&2
    return ${rc}
  fi
  echo "${out}"
  return 0
}

run_nmcli_capture() {
  set +e
  local out
  out="$("${NMCLI}" "$@" 2>&1)"
  local rc=$?
  set -e
  printf '%s' "${out}"
  return ${rc}
}

case "${CMD}" in
  scan)
    IFACE="${1:-$(detect_iface)}"
    run_nmcli -t -f IN-USE,SSID,BSSID,SIGNAL,SECURITY dev wifi list --rescan yes ifname "${IFACE}"
    ;;
  connect)
    SSID="${1:-}"
    ARG2="${2:-}"
    ARG3="${3:-}"
    ARG4="${4:-}"
    PASSWORD=""
    IFACE="$(detect_iface)"
    HIDDEN="no"
    if [[ -n "${ARG3}" ]]; then
      PASSWORD="${ARG2}"
      IFACE="${ARG3}"
      HIDDEN="${ARG4:-no}"
    elif [[ -n "${ARG2}" ]]; then
      IFACE="${ARG2}"
    fi
    if [[ -z "${SSID}" ]]; then
      echo "missing ssid" >&2
      exit 2
    fi
    if [[ -n "${PASSWORD}" ]]; then
      run_nmcli dev wifi connect "${SSID}" password "${PASSWORD}" ifname "${IFACE}" || run_nmcli dev wifi connect "${SSID}" password "${PASSWORD}" || true
      if [[ "${HIDDEN}" == "yes" ]]; then
        run_nmcli connection add type wifi con-name "${SSID}" ifname "${IFACE}" ssid "${SSID}" || true
        run_nmcli connection modify "${SSID}" 802-11-wireless.hidden yes 802-11-wireless-security.key-mgmt wpa-psk 802-11-wireless-security.psk "${PASSWORD}" || true
        run_nmcli connection up "${SSID}" || true
      fi
    else
      run_nmcli dev wifi connect "${SSID}" ifname "${IFACE}" || run_nmcli dev wifi connect "${SSID}" || true
      if [[ "${HIDDEN}" == "yes" ]]; then
        run_nmcli connection add type wifi con-name "${SSID}" ifname "${IFACE}" ssid "${SSID}" || true
        run_nmcli connection modify "${SSID}" 802-11-wireless.hidden yes || true
        run_nmcli connection up "${SSID}" || true
      fi
    fi
    run_nmcli -t -f GENERAL.STATE device show "${IFACE}" >/dev/null
    ;;
  profiles)
    run_nmcli -t -f NAME,UUID,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY connection show
    ;;
  profile-set)
    SSID="${1:-}"
    PRIO="${2:-0}"
    AUTO="${3:-yes}"
    if [[ -z "${SSID}" ]]; then
      echo "missing ssid" >&2
      exit 2
    fi
    if [[ "${AUTO}" != "yes" && "${AUTO}" != "no" ]]; then
      AUTO="yes"
    fi
    run_nmcli connection modify "${SSID}" connection.autoconnect "${AUTO}" connection.autoconnect-priority "${PRIO}"
    ;;
  profile-delete)
    SSID="${1:-}"
    UUID="${2:-}"
    if [[ -z "${SSID}" ]]; then
      echo "missing ssid" >&2
      exit 2
    fi
    ERRORS=()
    IFACE="$(detect_iface)"
    ACTIVE_CONN="$(run_nmcli_capture -t -f GENERAL.CONNECTION device show "${IFACE}" | sed -n 's/^GENERAL\.CONNECTION://p' | head -n1)"

    if [[ -n "${UUID}" ]]; then
      run_nmcli_capture connection down uuid "${UUID}" >/dev/null || true
      if run_nmcli_capture connection delete uuid "${UUID}" >/dev/null; then
        exit 0
      fi
      ERRORS+=("delete uuid ${UUID} failed")
    fi

    run_nmcli_capture connection down id "${SSID}" >/dev/null || true
    if run_nmcli_capture connection delete id "${SSID}" >/dev/null; then
      exit 0
    fi
    if run_nmcli_capture connection delete "${SSID}" >/dev/null; then
      exit 0
    fi
    ERRORS+=("delete id/name '${SSID}' failed")

    # Some WPS flows result in active runtime connection without persistent NM profile.
    # In that case disconnect the device and treat as successful user intent.
    if [[ -n "${ACTIVE_CONN}" && "${ACTIVE_CONN}" == "${SSID}" ]]; then
      run_nmcli_capture device disconnect "${IFACE}" >/dev/null || true
      echo "profile_not_found_disconnected_active=true"
      exit 0
    fi

    echo "could not delete profile: ${SSID}" >&2
    echo "${ERRORS[*]}" >&2
    exit 44
    ;;
  profile-up)
    SSID="${1:-}"
    if [[ -z "${SSID}" ]]; then
      echo "missing ssid" >&2
      exit 2
    fi
    run_nmcli connection up "${SSID}"
    ;;
  *)
    echo "usage: $0 <scan|connect|profiles|profile-set|profile-delete|profile-up> [args...]" >&2
    exit 2
    ;;
esac
