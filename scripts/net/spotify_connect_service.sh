#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-status}"
REQUESTED_SERVICE="${2:-}"

CANDIDATES_RAW="${SPOTIFY_CONNECT_SERVICE_CANDIDATES:-raspotify.service librespot.service}"
SERVICE_SCOPE="${SPOTIFY_CONNECT_SERVICE_SCOPE:-auto}"
SERVICE_USER="${SPOTIFY_CONNECT_SERVICE_USER:-${SUDO_USER:-}}"
if [[ -z "${SERVICE_USER}" ]]; then
  current_user="$(id -un 2>/dev/null || true)"
  if [[ -n "${current_user}" && "${current_user}" != "root" ]]; then
    SERVICE_USER="${current_user}"
  fi
fi
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

_user_id() {
  local user="$1"
  if [[ -z "${user}" ]]; then
    return 1
  fi
  id -u "${user}" 2>/dev/null || true
}

_user_home() {
  local user="$1"
  if [[ -z "${user}" ]]; then
    return 1
  fi
  getent passwd "${user}" | awk -F: '{print $6}' | head -n1
}

_user_env() {
  local user="$1"
  local uid
  uid="$(_user_id "${user}")"
  if [[ -z "${uid}" ]]; then
    return 1
  fi
  local runtime_dir="/run/user/${uid}"
  if [[ ! -d "${runtime_dir}" ]]; then
    return 1
  fi
  printf 'XDG_RUNTIME_DIR=%s DBUS_SESSION_BUS_ADDRESS=unix:path=%s/bus' "${runtime_dir}" "${runtime_dir}"
}

run_with_timeout() {
  local seconds="$1"
  shift
  "$@" &
  local pid=$!
  ( sleep "${seconds}"; kill -0 "${pid}" >/dev/null 2>&1 && kill -TERM "${pid}" >/dev/null 2>&1 ) &
  local killer=$!
  wait "${pid}" 2>/dev/null
  local rc=$?
  kill -TERM "${killer}" >/dev/null 2>&1 || true
  return "${rc}"
}

_systemctl_user() {
  local user="$1"
  shift
  local env
  env="$(_user_env "${user}")" || return 1
  if command -v timeout >/dev/null 2>&1; then
    sudo -n -u "${user}" env ${env} timeout 4 systemctl --user "$@"
  else
    run_with_timeout 4 sudo -n -u "${user}" env ${env} systemctl --user "$@"
  fi
}

_user_proc_running() {
  local user="$1"
  if [[ -z "${user}" ]]; then
    return 1
  fi
  pgrep -u "${user}" -f 'raspotify|librespot' >/dev/null 2>&1
}

_user_proc_stop() {
  local user="$1"
  if [[ -z "${user}" ]]; then
    return 1
  fi
  pkill -u "${user}" -f 'raspotify|librespot' >/dev/null 2>&1 || true
}

_user_proc_start() {
  local user="$1"
  if [[ -z "${user}" ]]; then
    return 1
  fi
  sudo -n -u "${user}" bash -lc 'set -a; [ -f ~/.config/raspotify/conf ] && . ~/.config/raspotify/conf; [ -f ~/.config/raspotify/env ] && . ~/.config/raspotify/env; [ -f /etc/default/raspotify ] && . /etc/default/raspotify; set +a; BIN=""; [ -x /usr/bin/raspotify ] && BIN="/usr/bin/raspotify"; [ -z "$BIN" ] && [ -x /usr/bin/librespot ] && BIN="/usr/bin/librespot"; [ -z "$BIN" ] && exit 0; nohup $BIN ${OPTIONS:-} >/tmp/raspotify.log 2>&1 & disown' >/dev/null 2>&1 || true
}

_user_marker_path() {
  local user="$1"
  local home
  home="$(_user_home "${user}")"
  if [[ -z "${home}" ]]; then
    echo ""
    return 1
  fi
  echo "${home}/.config/raspotify/.portal_enabled"
}

_user_marker_enabled() {
  local user="$1"
  local marker
  marker="$(_user_marker_path "${user}")"
  [[ -n "${marker}" && -f "${marker}" ]]
}

_user_unit_exists() {
  local user="$1"
  local service="${2:-raspotify.service}"
  local home
  home="$(_user_home "${user}")"
  if [[ -z "${home}" ]]; then
    return 1
  fi
  [[ -f "${home}/.config/systemd/user/${service}" ]]
}

choose_service_system() {
  local candidate load_state
  for candidate in ${CANDIDATES_RAW}; do
    load_state="$(systemctl show "${candidate}" --property=LoadState --value 2>/dev/null || true)"
    if [[ -n "${load_state}" && "${load_state}" != "not-found" && "${load_state}" != "masked" ]]; then
      echo "${candidate}"
      return 0
    fi
  done
  return 1
}

choose_service_user() {
  local user="${SERVICE_USER}"
  if [[ -z "${user}" ]]; then
    return 1
  fi
  local candidate load_state
  for candidate in ${CANDIDATES_RAW}; do
    load_state="$(_systemctl_user "${user}" show "${candidate}" --property=LoadState --value 2>/dev/null || true)"
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
  local service_scope="${2:-system}"
  local installed enabled_state active_state sub_state
  local enabled running device_name backend output_device last_error checked_at
  local config_path=""
  local user_home=""

  if [[ -z "${service_name}" ]]; then
    service_name="${REQUESTED_SERVICE:-raspotify.service}"
    installed="false"
    enabled_state="not-found"
    active_state="inactive"
    sub_state="dead"
  else
    installed="true"
    if [[ "${service_scope}" == "user" ]]; then
      enabled_state="$(_systemctl_user "${SERVICE_USER}" is-enabled "${service_name}" 2>/dev/null || true)"
      active_state="$(_systemctl_user "${SERVICE_USER}" show "${service_name}" --property=ActiveState --value 2>/dev/null || true)"
      sub_state="$(_systemctl_user "${SERVICE_USER}" show "${service_name}" --property=SubState --value 2>/dev/null || true)"
      if [[ -z "${enabled_state}" && -z "${active_state}" ]]; then
        local marker
        marker="$(_user_marker_path "${SERVICE_USER}")"
        if [[ -n "${marker}" && -f "${marker}" ]]; then
          enabled_state="enabled"
        fi
        if _user_proc_running "${SERVICE_USER}"; then
          active_state="active"
          sub_state="running"
        else
          active_state="inactive"
          sub_state="dead"
        fi
      fi
    else
      enabled_state="$(systemctl is-enabled "${service_name}" 2>/dev/null || true)"
      active_state="$(systemctl show "${service_name}" --property=ActiveState --value 2>/dev/null || true)"
      sub_state="$(systemctl show "${service_name}" --property=SubState --value 2>/dev/null || true)"
    fi
  fi

  enabled="false"
  case "${enabled_state}" in
    enabled|static|indirect|generated|linked|alias) enabled="true" ;;
  esac

  running="$(normalize_bool "$([[ "${active_state}" == "active" ]] && echo true || echo false)")"

  device_name=""
  backend=""
  output_device=""

  if [[ "${service_scope}" == "user" ]]; then
    user_home="$(_user_home "${SERVICE_USER}")"
    if [[ -n "${user_home}" && -f "${user_home}/.config/raspotify/conf" ]]; then
      config_path="${user_home}/.config/raspotify/conf"
    elif [[ -n "${user_home}" && -f "${user_home}/.config/raspotify/env" ]]; then
      config_path="${user_home}/.config/raspotify/env"
    fi
  fi

  if [[ -n "${config_path}" ]]; then
    device_name="$(read_kv_file "${config_path}" DEVICE_NAME || true)"
    backend="$(read_kv_file "${config_path}" BACKEND || true)"
    output_device="$(read_kv_file "${config_path}" DEVICE || true)"
    local opts
    opts="$(read_kv_file "${config_path}" OPTIONS || true)"
    if [[ -z "${device_name}" ]]; then
      device_name="$(extract_from_options "${opts}" '--name')"
    fi
    if [[ -z "${backend}" ]]; then
      backend="$(extract_from_options "${opts}" '--backend')"
    fi
    if [[ -z "${output_device}" ]]; then
      output_device="$(extract_from_options "${opts}" '--device')"
    fi
  elif [[ "${service_name}" == "raspotify.service" || -f /etc/default/raspotify ]]; then
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
    if [[ "${service_scope}" == "user" ]]; then
      local env_line
      env_line="$(_user_env "${SERVICE_USER}")" || env_line=""
      if [[ -n "${env_line}" ]]; then
        last_error="$(sudo -n -u "${SERVICE_USER}" env ${env_line} journalctl --user -u "${service_name}" -p err -n 10 --no-pager -o cat 2>/dev/null | tail -n1 | tr -d '\r' | sed 's/^\s\+//; s/\s\+$//')"
      else
        last_error=""
      fi
    else
      last_error="$(journalctl -u "${service_name}" -p err -n 10 --no-pager -o cat 2>/dev/null | tail -n1 | tr -d '\r' | sed 's/^\s\+//; s/\s\+$//')"
    fi
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
  emit "service_scope" "${service_scope}"
  emit "service_installed" "${installed}"
  emit "service_enabled" "${enabled}"
  emit "service_running" "${running}"
  emit "service_enabled_state" "${enabled_state}"
  emit "service_active_state" "${active_state}"
  emit "service_sub_state" "${sub_state}"
  emit "device_name" "${device_name}"
  emit "backend" "${backend}"
  emit "output_device" "${output_device}"
  emit "config_path" "${config_path}"
  emit "last_error" "${last_error}"
  emit "connect_ready" "${connect_ready}"
  emit "checked_at" "${checked_at}"
  emit "message" "Spotify Connect ${ACTION} processed"
}

chosen_service=""
chosen_scope="system"
first_candidate="${CANDIDATES_RAW%% *}"
if [[ "${SERVICE_SCOPE}" == "user" ]]; then
  if chosen_service="$(choose_service_user)"; then
    chosen_scope="user"
  else
    chosen_scope="user"
    chosen_service="${REQUESTED_SERVICE:-${first_candidate}}"
  fi
elif [[ "${SERVICE_SCOPE}" == "system" ]]; then
  if chosen_service="$(choose_service_system)"; then
    chosen_scope="system"
  else
    chosen_service=""
  fi
else
  # auto: prefer user scope when a service user is configured
  if [[ -n "${SERVICE_USER}" ]]; then
    if _user_proc_running "${SERVICE_USER}" || _user_marker_enabled "${SERVICE_USER}" || _user_unit_exists "${SERVICE_USER}" "${REQUESTED_SERVICE:-${first_candidate}}"; then
      chosen_scope="user"
      chosen_service="${REQUESTED_SERVICE:-${first_candidate}}"
    elif chosen_service="$(choose_service_user)"; then
      chosen_scope="user"
    elif chosen_service="$(choose_service_system)"; then
      chosen_scope="system"
    else
      chosen_scope="user"
      chosen_service="${REQUESTED_SERVICE:-${first_candidate}}"
    fi
  else
    if chosen_service="$(choose_service_system)"; then
      chosen_scope="system"
    elif chosen_service="$(choose_service_user)"; then
      chosen_scope="user"
    else
      chosen_service=""
    fi
  fi
fi

case "${ACTION}" in
  status|refresh)
    build_status "${chosen_service}" "${chosen_scope}"
    ;;
  start|stop|restart|enable|disable)
    if [[ -z "${chosen_service}" ]]; then
      emit "success" "false"
      emit "code" "service_not_found"
      emit "message" "No Spotify Connect service found"
      emit "details" "Checked: ${CANDIDATES_RAW}"
      exit 4
    fi
    if [[ "${chosen_scope}" == "user" ]]; then
      if ! _systemctl_user "${SERVICE_USER}" "${ACTION}" "${chosen_service}" >/dev/null 2>&1; then
        # Fallback: process control when user systemd is unavailable
        if [[ "${ACTION}" == "enable" ]]; then
          marker="$(_user_marker_path "${SERVICE_USER}")"
          if [[ -n "${marker}" ]]; then
            sudo -n -u "${SERVICE_USER}" mkdir -p "$(dirname "${marker}")"
            sudo -n -u "${SERVICE_USER}" touch "${marker}"
          fi
        elif [[ "${ACTION}" == "disable" ]]; then
          marker="$(_user_marker_path "${SERVICE_USER}")"
          if [[ -n "${marker}" && -f "${marker}" ]]; then
            sudo -n -u "${SERVICE_USER}" rm -f "${marker}"
          fi
          _user_proc_stop "${SERVICE_USER}"
        elif [[ "${ACTION}" == "start" ]]; then
          _user_proc_start "${SERVICE_USER}"
        elif [[ "${ACTION}" == "stop" ]]; then
          _user_proc_stop "${SERVICE_USER}"
        elif [[ "${ACTION}" == "restart" ]]; then
          _user_proc_stop "${SERVICE_USER}"
          _user_proc_start "${SERVICE_USER}"
        fi
      fi
    elif ! systemctl "${ACTION}" "${chosen_service}" >/dev/null 2>&1; then
      emit "success" "false"
      emit "code" "service_action_failed"
      emit "message" "Failed to ${ACTION} service"
      emit "service_name" "${chosen_service}"
      emit "service_scope" "${chosen_scope}"
      exit 5
    fi
    build_status "${chosen_service}" "${chosen_scope}"
    ;;
  *)
    emit "success" "false"
    emit "code" "invalid_action"
    emit "message" "Action must be start|stop|restart|enable|disable|status|refresh"
    exit 2
    ;;
esac
