#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${1:-}"
SERVICE_USER="${2:-}"
SERVICE_NAME="${3:-joormann-media-deviceplayer.service}"
PORTAL_DIR="${4:-}"

emit() {
  local key="$1"
  local val="${2:-}"
  printf '%s=%s\n' "$key" "$val"
}

if [[ -z "${REPO_DIR}" || -z "${SERVICE_USER}" || -z "${PORTAL_DIR}" ]]; then
  emit "success" "false"
  emit "code" "invalid_args"
  emit "message" "Usage: player_service_install.sh <repo_dir> <service_user> <service_name> <portal_dir>"
  exit 2
fi

if ! command -v systemctl >/dev/null 2>&1; then
  emit "success" "false"
  emit "code" "systemctl_missing"
  emit "message" "systemctl command not found"
  exit 3
fi

if [[ ! -d "${REPO_DIR}" ]]; then
  emit "success" "false"
  emit "code" "repo_missing"
  emit "message" "Player repo dir not found"
  emit "repo_dir" "${REPO_DIR}"
  exit 4
fi

if [[ ! -d "${PORTAL_DIR}" ]]; then
  emit "success" "false"
  emit "code" "portal_missing"
  emit "message" "Portal dir not found"
  emit "portal_dir" "${PORTAL_DIR}"
  exit 5
fi

home_dir="$(eval echo "~${SERVICE_USER}")"
if [[ -z "${home_dir}" || ! -d "${home_dir}" ]]; then
  emit "success" "false"
  emit "code" "user_home_missing"
  emit "message" "Service user home dir not found"
  emit "service_user" "${SERVICE_USER}"
  exit 6
fi

python_bin="${REPO_DIR}/.venv/bin/python"
if [[ ! -x "${python_bin}" ]]; then
  python_bin="$(command -v python3 || true)"
fi
if [[ -z "${python_bin}" ]]; then
  emit "success" "false"
  emit "code" "python_missing"
  emit "message" "No python interpreter found"
  exit 7
fi

unit_path="/etc/systemd/system/${SERVICE_NAME}"

cat > "${unit_path}" <<EOF
[Unit]
Description=Joormann Media DevicePlayer
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
SupplementaryGroups=video render input audio
WorkingDirectory=${REPO_DIR}
Environment=PYTHONUNBUFFERED=1
Environment=SDL_AUDIODRIVER=dummy
Environment=DEVICEPLAYER_MANIFEST_PATH=
Environment=DEVICEPLAYER_STORAGE_ROOT=
Environment=HOME=${home_dir}
Environment=DEVICEPLAYER_PORTAL_PLAYER_SOURCE=${PORTAL_DIR}/var/data/player-source.json
Environment=DEVICEPLAYER_PORTAL_STORAGE_CONFIG=${PORTAL_DIR}/var/data/config-storage.json
Environment=DEVICEPLAYER_VIDEO_DRIVERS=kmsdrm,fbcon,wayland,x11
Environment=DEVICEPLAYER_TRANSITION_FPS=30
Environment=DEVICEPLAYER_IDLE_SLEEP_MS=200
Environment=DEVICEPLAYER_CONTROL_API_HOST=127.0.0.1
Environment=DEVICEPLAYER_CONTROL_API_PORT=5081
Environment=DEVICEPLAYER_AUDIO_DEFAULT_OUTPUT=local
Environment=DEVICEPLAYER_AUDIO_DEFAULT_VOLUME=65
Environment=DEVICEPLAYER_AUDIO_ALLOWED_ROOT=/mnt/deviceportal/media/stream/current/audio
ExecStart=${python_bin} ${REPO_DIR}/run.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

chmod 0644 "${unit_path}"

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}" >/dev/null 2>&1 || true
systemctl restart "${SERVICE_NAME}" >/dev/null 2>&1 || true

active_state="$(systemctl show "${SERVICE_NAME}" --property=ActiveState --value 2>/dev/null || true)"
substate="$(systemctl show "${SERVICE_NAME}" --property=SubState --value 2>/dev/null || true)"

emit "success" "true"
emit "service_name" "${SERVICE_NAME}"
emit "repo_dir" "${REPO_DIR}"
emit "portal_dir" "${PORTAL_DIR}"
emit "service_user" "${SERVICE_USER}"
emit "active_state" "${active_state}"
emit "substate" "${substate}"
emit "message" "Player service installed"
