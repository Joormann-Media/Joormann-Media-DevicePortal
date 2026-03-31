#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-install}"
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

if [[ "${ACTION}" != "install" ]]; then
  emit "success" "false"
  emit "code" "invalid_action"
  emit "message" "Action must be install"
  exit 2
fi

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

_systemctl_user() {
  local user="$1"
  shift
  local env
  env="$(_user_env "${user}")" || return 1
  sudo -n -u "${user}" env ${env} systemctl --user "$@"
}

choose_service_system() {
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

ensure_pkg() {
  if command -v raspotify >/dev/null 2>&1 || command -v librespot >/dev/null 2>&1; then
    return 0
  fi
  if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get update -y >/dev/null 2>&1 || true
    DEBIAN_FRONTEND=noninteractive apt-get install -y raspotify >/dev/null 2>&1 || true
  fi
  if command -v raspotify >/dev/null 2>&1 || command -v librespot >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

resolve_bin() {
  if command -v raspotify >/dev/null 2>&1; then
    echo "/usr/bin/raspotify"
    return 0
  fi
  if command -v librespot >/dev/null 2>&1; then
    echo "/usr/bin/librespot"
    return 0
  fi
  return 1
}

install_user_unit() {
  local user="${SERVICE_USER}"
  if [[ -z "${user}" ]]; then
    emit "success" "false"
    emit "code" "user_missing"
    emit "message" "User scope requested but no service user provided"
    exit 4
  fi
  local home
  home="$(_user_home "${user}")"
  if [[ -z "${home}" ]]; then
    emit "success" "false"
    emit "code" "user_home_missing"
    emit "message" "Could not resolve user home"
    exit 4
  fi
  local unit_dir="${home}/.config/systemd/user"
  local service_name
  service_name="${REQUESTED_SERVICE:-raspotify.service}"
  local bin_path
  bin_path="$(resolve_bin 2>/dev/null || true)"
  if [[ -z "${bin_path}" ]]; then
    emit "success" "false"
    emit "code" "raspotify_missing"
    emit "message" "raspotify/librespot not installed"
    exit 5
  fi
  sudo -n -u "${user}" mkdir -p "${unit_dir}"
  sudo -n -u "${user}" tee "${unit_dir}/${service_name}" >/dev/null <<'EOF'
[Unit]
Description=Spotify Connect (raspotify) - User Service
After=network-online.target
Wants=network-online.target

[Service]
EnvironmentFile=-%h/.config/raspotify/conf
EnvironmentFile=-%h/.config/raspotify/env
EnvironmentFile=-/etc/default/raspotify
ExecStart=%BIN_PATH%
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF
  sudo -n -u "${user}" sed -i "s#%BIN_PATH%#${bin_path}#g" "${unit_dir}/${service_name}"
  if _systemctl_user "${user}" daemon-reload; then
    _systemctl_user "${user}" enable "${service_name}" || true
  else
    # no user systemd available: mark as enabled for portal fallback
    mkdir -p "${home}/.config/raspotify" >/dev/null 2>&1 || true
    touch "${home}/.config/raspotify/.portal_enabled" >/dev/null 2>&1 || true
  fi
}

install_system_unit() {
  local service_name
  service_name="${REQUESTED_SERVICE:-raspotify.service}"
  local load_state
  load_state="$(systemctl show "${service_name}" --property=LoadState --value 2>/dev/null || true)"
  local bin_path
  bin_path="$(resolve_bin 2>/dev/null || true)"
  if [[ -z "${bin_path}" ]]; then
    emit "success" "false"
    emit "code" "raspotify_missing"
    emit "message" "raspotify/librespot not installed"
    exit 5
  fi
  if [[ -z "${load_state}" || "${load_state}" == "not-found" ]]; then
    cat >/etc/systemd/system/${service_name} <<'EOF'
[Unit]
Description=Spotify Connect (raspotify)
After=network-online.target
Wants=network-online.target

[Service]
EnvironmentFile=-/etc/default/raspotify
ExecStart=%BIN_PATH%
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
    sed -i "s#%BIN_PATH%#${bin_path}#g" "/etc/systemd/system/${service_name}"
    systemctl daemon-reload
  fi
  systemctl enable "${service_name}" >/dev/null 2>&1 || true
}

if ! ensure_pkg; then
  emit "success" "false"
  emit "code" "raspotify_missing"
  emit "message" "raspotify/librespot not installed"
  exit 5
fi

BIN_PATH="$(resolve_bin 2>/dev/null || true)"
if [[ -z "${BIN_PATH}" ]]; then
  emit "success" "false"
  emit "code" "raspotify_missing"
  emit "message" "raspotify/librespot not installed"
  exit 5
fi

chosen_scope="${SERVICE_SCOPE}"
if [[ "${chosen_scope}" == "auto" || -z "${chosen_scope}" ]]; then
  if [[ -n "${SERVICE_USER}" ]]; then
    chosen_scope="user"
  elif choose_service_user >/dev/null 2>&1; then
    chosen_scope="user"
  elif choose_service_system >/dev/null 2>&1; then
    chosen_scope="system"
  else
    chosen_scope="system"
  fi
fi

if [[ "${chosen_scope}" == "user" ]]; then
  install_user_unit
else
  install_system_unit
fi

emit "success" "true"
emit "action" "install"
emit "service_scope" "${chosen_scope}"
emit "service_name" "${REQUESTED_SERVICE:-raspotify.service}"
emit "message" "Spotify Connect service installed"
exit 0
