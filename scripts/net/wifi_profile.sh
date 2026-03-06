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

is_unknown_connection_error() {
  local txt="${1:-}"
  txt="$(echo "${txt}" | tr '[:upper:]' '[:lower:]')"
  [[ "${txt}" == *"unknown connection"* || "${txt}" == *"unknown connections"* || "${txt}" == *"unbekannte verbindung"* ]]
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

    UUID_DELETE_ERR=""
    ID_DELETE_ERR=""
    NAME_DELETE_ERR=""

    if [[ -n "${UUID}" ]]; then
      run_nmcli_capture connection down uuid "${UUID}" >/dev/null || true
      set +e
      UUID_DELETE_ERR="$(run_nmcli_capture connection delete uuid "${UUID}")"
      UUID_DELETE_RC=$?
      set -e
      if [[ ${UUID_DELETE_RC} -eq 0 ]]; then
        exit 0
      fi
      ERRORS+=("delete uuid ${UUID} failed")
    fi

    run_nmcli_capture connection down id "${SSID}" >/dev/null || true
    set +e
    ID_DELETE_ERR="$(run_nmcli_capture connection delete id "${SSID}")"
    ID_DELETE_RC=$?
    set -e
    if [[ ${ID_DELETE_RC} -eq 0 ]]; then
      exit 0
    fi
    set +e
    NAME_DELETE_ERR="$(run_nmcli_capture connection delete "${SSID}")"
    NAME_DELETE_RC=$?
    set -e
    if [[ ${NAME_DELETE_RC} -eq 0 ]]; then
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

    if is_unknown_connection_error "${UUID_DELETE_ERR} ${ID_DELETE_ERR} ${NAME_DELETE_ERR}"; then
      echo "profile_missing=true"
      exit 0
    fi

    echo "could not delete profile: ${SSID}" >&2
    echo "${ERRORS[*]}" >&2
    exit 44
    ;;
  profile-up)
    SSID="${1:-}"
    UUID="${2:-}"
    if [[ -z "${SSID}" ]]; then
      echo "missing ssid" >&2
      exit 2
    fi
    IFACE="$(detect_iface)"

    PROFILE_UP_ERRS=()

    if [[ -n "${UUID}" ]]; then
      set +e
      UUID_UP_OUT="$(run_nmcli_capture connection up uuid "${UUID}")"
      UUID_UP_RC=$?
      set -e
      if [[ ${UUID_UP_RC} -eq 0 ]]; then
        exit 0
      fi
      PROFILE_UP_ERRS+=("${UUID_UP_OUT}")
    fi

    set +e
    ID_UP_OUT="$(run_nmcli_capture connection up id "${SSID}")"
    ID_UP_RC=$?
    set -e
    if [[ ${ID_UP_RC} -eq 0 ]]; then
      exit 0
    fi
    PROFILE_UP_ERRS+=("${ID_UP_OUT}")

    # Fallback: match by stored Wi-Fi SSID (connection.id may differ from SSID).
    CANDIDATE_UUID="$("${NMCLI}" -t -f NAME,UUID,TYPE,802-11-wireless.ssid connection show 2>/dev/null | awk -F: -v s="${SSID}" '$3=="802-11-wireless" && ($1==s || $4==s) {print $2; exit}')"
    if [[ -n "${CANDIDATE_UUID}" ]]; then
      set +e
      CANDIDATE_UP_OUT="$(run_nmcli_capture connection up uuid "${CANDIDATE_UUID}")"
      CANDIDATE_UP_RC=$?
      set -e
      if [[ ${CANDIDATE_UP_RC} -eq 0 ]]; then
        exit 0
      fi
      PROFILE_UP_ERRS+=("${CANDIDATE_UP_OUT}")
    fi

    # If already connected to requested SSID, treat as success.
    ACTIVE_SSID="$("${NMCLI}" -t -f ACTIVE,SSID dev wifi list ifname "${IFACE}" 2>/dev/null | awk -F: '$1=="yes"||$1=="*" {print $2; exit}')"
    if [[ -n "${ACTIVE_SSID}" && "${ACTIVE_SSID}" == "${SSID}" ]]; then
      echo "already_connected=true"
      exit 0
    fi

    echo "${PROFILE_UP_ERRS[*]}" >&2
    exit 46
    ;;
  *)
    echo "usage: $0 <scan|connect|profiles|profile-set|profile-delete|profile-up> [args...]" >&2
    exit 2
    ;;
esac
