#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-status}"
REQUESTED_SERVICE="${2:-}"

CANDIDATES_RAW="${SPOTIFY_CONNECT_SERVICE_CANDIDATES:-raspotify.service librespot.service}"
if [[ -n "${REQUESTED_SERVICE}" ]]; then
  CANDIDATES_RAW="${REQUESTED_SERVICE}"
fi

emit() {
  local key="$1"
  local val="${2:-}"
  printf '%s=%s\n' "$key" "$val"
}

normalize_bool() {
  local raw="${1:-}"
  case "${raw,,}" in
    1|true|yes|on|enabled|active) echo "true" ;;
    *) echo "false" ;;
  esac
}

if ! command -v systemctl >/dev/null 2>&1; then
  emit "success" "false"
  emit "code" "systemctl_missing"
  emit "message" "systemctl command not found"
  exit 3
fi

choose_service() {
  local candidate load_state
  for candidate in ${CANDIDATES_RAW}; do
    load_state="$(systemctl show "${candidate}" --property=LoadState --value 2>/dev/null || true)"
    if [[ -n "${load_state}" && "${load_state}" != "not-found" ]]; then
      echo "${candidate}"
      return 0
    fi
  done
  return 1
}

read_kv_file() {
  local file_path="$1"
  local key="$2"
  [[ -f "${file_path}" ]] || return 1
  sed -n "s/^${key}=//p" "${file_path}" | tail -n1 | sed 's/^"//; s/"$//' | sed "s/^'//; s/'$//" | tr -d '\r' | sed 's/^\s\+//; s/\s\+$//'
}

extract_from_options() {
  local options="$1"
  local flag="$2"
  if [[ -z "${options}" ]]; then
    echo ""
    return 0
  fi

  local value
  value="$(printf '%s' "${options}" | sed -nE "s/.*${flag}[= ]([^[:space:]]+).*/\\1/p" | head -n1 | sed 's/^"//; s/"$//; s/^\x27//; s/\x27$//')"
  printf '%s' "${value}"
}

build_status() {
  local service_name="$1"
  local installed enabled_state active_state sub_state
  local enabled running device_name backend output_device last_error checked_at

  if [[ -z "${service_name}" ]]; then
    service_name="${REQUESTED_SERVICE:-raspotify.service}"
    installed="false"
    enabled_state="not-found"
    active_state="inactive"
    sub_state="dead"
  else
    installed="true"
    enabled_state="$(systemctl is-enabled "${service_name}" 2>/dev/null || true)"
    active_state="$(systemctl show "${service_name}" --property=ActiveState --value 2>/dev/null || true)"
    sub_state="$(systemctl show "${service_name}" --property=SubState --value 2>/dev/null || true)"
  fi

  enabled="false"
  case "${enabled_state}" in
    enabled|static|indirect|generated|linked|alias) enabled="true" ;;
  esac

  running="$(normalize_bool "$([[ "${active_state}" == "active" ]] && echo true || echo false)")"

  device_name=""
  backend=""
  output_device=""

  if [[ "${service_name}" == "raspotify.service" || -f /etc/default/raspotify ]]; then
    device_name="$(read_kv_file /etc/default/raspotify DEVICE_NAME || true)"
    backend="$(read_kv_file /etc/default/raspotify BACKEND || true)"
    output_device="$(read_kv_file /etc/default/raspotify DEVICE || true)"
    local opts
    opts="$(read_kv_file /etc/default/raspotify OPTIONS || true)"
    if [[ -z "${device_name}" ]]; then
      device_name="$(extract_from_options "${opts}" '--name')"
    fi
    if [[ -z "${backend}" ]]; then
      backend="$(extract_from_options "${opts}" '--backend')"
    fi
    if [[ -z "${output_device}" ]]; then
      output_device="$(extract_from_options "${opts}" '--device')"
    fi
  fi

  if [[ -z "${device_name}" && -f /etc/default/librespot ]]; then
    device_name="$(read_kv_file /etc/default/librespot DEVICE_NAME || true)"
    backend="${backend:-$(read_kv_file /etc/default/librespot BACKEND || true)}"
    output_device="${output_device:-$(read_kv_file /etc/default/librespot DEVICE || true)}"
    local librespot_opts
    librespot_opts="$(read_kv_file /etc/default/librespot OPTIONS || true)"
    if [[ -z "${device_name}" ]]; then
      device_name="$(extract_from_options "${librespot_opts}" '--name')"
    fi
    if [[ -z "${backend}" ]]; then
      backend="$(extract_from_options "${librespot_opts}" '--backend')"
    fi
    if [[ -z "${output_device}" ]]; then
      output_device="$(extract_from_options "${librespot_opts}" '--device')"
    fi
  fi

  if [[ -z "${backend}" && -f /etc/librespot.conf ]]; then
    backend="$(grep -E '^\s*backend\s*=' /etc/librespot.conf 2>/dev/null | head -n1 | sed -E 's/^\s*backend\s*=\s*//; s/^"//; s/"$//')"
  fi

  if [[ -z "${device_name}" ]]; then
    device_name="$(hostname 2>/dev/null || echo '')"
  fi

  if [[ -n "${service_name}" ]]; then
    last_error="$(journalctl -u "${service_name}" -p err -n 10 --no-pager -o cat 2>/dev/null | tail -n1 | tr -d '\r' | sed 's/^\s\+//; s/\s\+$//')"
  else
    last_error=""
  fi

  local connect_ready="false"
  if [[ "${installed}" == "true" && "${enabled}" == "true" && "${running}" == "true" ]]; then
    connect_ready="true"
  fi

  checked_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  emit "success" "true"
  emit "action" "${ACTION}"
  emit "service_name" "${service_name}"
  emit "service_installed" "${installed}"
  emit "service_enabled" "${enabled}"
  emit "service_running" "${running}"
  emit "service_enabled_state" "${enabled_state}"
  emit "service_active_state" "${active_state}"
  emit "service_sub_state" "${sub_state}"
  emit "device_name" "${device_name}"
  emit "backend" "${backend}"
  emit "output_device" "${output_device}"
  emit "last_error" "${last_error}"
  emit "connect_ready" "${connect_ready}"
  emit "checked_at" "${checked_at}"
  emit "message" "Spotify Connect ${ACTION} processed"
}

chosen_service=""
if chosen_service="$(choose_service)"; then
  :
else
  chosen_service=""
fi

case "${ACTION}" in
  status|refresh)
    build_status "${chosen_service}"
    ;;
  start|stop|restart)
    if [[ -z "${chosen_service}" ]]; then
      emit "success" "false"
      emit "code" "service_not_found"
      emit "message" "No Spotify Connect service found"
      emit "details" "Checked: ${CANDIDATES_RAW}"
      exit 4
    fi
    if ! systemctl "${ACTION}" "${chosen_service}" >/dev/null 2>&1; then
      emit "success" "false"
      emit "code" "service_action_failed"
      emit "message" "Failed to ${ACTION} service"
      emit "service_name" "${chosen_service}"
      exit 5
    fi
    build_status "${chosen_service}"
    ;;
  *)
    emit "success" "false"
    emit "code" "invalid_action"
    emit "message" "Action must be start|stop|restart|status|refresh"
    exit 2
    ;;
esac
