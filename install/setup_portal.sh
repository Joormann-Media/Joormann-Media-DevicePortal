#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo ./install/setup_portal.sh" >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_REPO_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
REPO_DIR="${1:-$DEFAULT_REPO_DIR}"
SERVICE_USER="${2:-${SUDO_USER:-www-data}}"
SERVICE_GROUP="$(id -gn "$SERVICE_USER" 2>/dev/null || echo "$SERVICE_USER")"
VENV_DIR="$REPO_DIR/.venv"
REQUIREMENTS_FILE="$REPO_DIR/requirements.txt"
SERVICE_FILE_DST="/etc/systemd/system/device-portal.service"
ENV_FILE="/etc/default/jm-deviceportal"
INTERNAL_STORAGE_SETUP="$REPO_DIR/install/setup_internal_storage.sh"
OLD_CONFIG_PATH=""
OLD_STORAGE_CONFIG_PATH=""
OLD_DEVICE_PATH=""
OLD_FINGERPRINT_PATH=""
OLD_STATE_PATH=""
OLD_PLAN_PATH=""

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE" || true
  OLD_CONFIG_PATH="${CONFIG_PATH:-}"
  OLD_STORAGE_CONFIG_PATH="${STORAGE_CONFIG_PATH:-}"
  OLD_DEVICE_PATH="${DEVICE_PATH:-}"
  OLD_FINGERPRINT_PATH="${FINGERPRINT_PATH:-}"
  OLD_STATE_PATH="${STATE_PATH:-}"
  OLD_PLAN_PATH="${PLAN_PATH:-}"
fi

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "Service user does not exist: $SERVICE_USER" >&2
  echo "Usage: sudo ./install/setup_portal.sh [REPO_DIR] [SERVICE_USER]" >&2
  exit 2
fi

if [[ ! -d "$REPO_DIR" ]]; then
  echo "Repository directory not found: $REPO_DIR" >&2
  exit 2
fi

is_pkg_installed() {
  dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "install ok installed"
}

is_raspotify_installed() {
  if is_pkg_installed "raspotify"; then
    return 0
  fi
  if command -v raspotify >/dev/null 2>&1; then
    return 0
  fi
  if systemctl list-unit-files --type=service 2>/dev/null | grep -q '^raspotify\.service'; then
    return 0
  fi
  return 1
}

MISSING_PKGS=()
for pkg in python3 python3-venv python3-pip curl mpg123; do
  if ! is_pkg_installed "$pkg"; then
    MISSING_PKGS+=("$pkg")
  fi
done

if [[ ${#MISSING_PKGS[@]} -gt 0 ]]; then
  echo "Installing missing packages: ${MISSING_PKGS[*]}"
  apt-get update
  apt-get install -y "${MISSING_PKGS[@]}"
else
  echo "All required base packages already installed (python3, python3-venv, python3-pip, curl, mpg123)."
fi

if command -v raspi-config >/dev/null 2>&1; then
  # Keep Wi-Fi country setup idempotent on Raspberry Pi OS hosts.
  raspi-config nonint do_wifi_country DE || true
fi

if ! is_raspotify_installed; then
  echo "Installing raspotify via upstream install script ..."
  curl -fsSL https://dtcooper.github.io/raspotify/install.sh | sh
else
  echo "raspotify already installed."
fi

install -d -m 0775 -o "$SERVICE_USER" -g "$SERVICE_USER" "$REPO_DIR/var"
install -d -m 0775 -o "$SERVICE_USER" -g "$SERVICE_USER" "$REPO_DIR/var/data"
install -d -m 0775 -o "$SERVICE_USER" -g "$SERVICE_USER" "$REPO_DIR/var/assets"

# Migrate legacy persisted files from /etc/device to repository-local data directory.
for f in config.json device.json fingerprint.json state.json plan.json; do
  if [[ -f "/etc/device/${f}" && ! -f "$REPO_DIR/var/data/${f}" ]]; then
    cp "/etc/device/${f}" "$REPO_DIR/var/data/${f}"
    chown "$SERVICE_USER:$SERVICE_USER" "$REPO_DIR/var/data/${f}"
    chmod 0640 "$REPO_DIR/var/data/${f}" || true
  fi
done

# Preserve runtime JSON from a previously configured portal path (important when
# service/env is reinstalled and points to a new repository directory).
copy_if_missing() {
  local src="$1"
  local dst="$2"
  if [[ -n "$src" && -f "$src" && ! -f "$dst" ]]; then
    cp "$src" "$dst"
    chown "$SERVICE_USER:$SERVICE_USER" "$dst"
    chmod 0640 "$dst" || true
  fi
}

copy_if_missing "$OLD_CONFIG_PATH" "$REPO_DIR/var/data/config.json"
copy_if_missing "$OLD_STORAGE_CONFIG_PATH" "$REPO_DIR/var/data/config-storage.json"
copy_if_missing "$OLD_DEVICE_PATH" "$REPO_DIR/var/data/device.json"
copy_if_missing "$OLD_FINGERPRINT_PATH" "$REPO_DIR/var/data/fingerprint.json"
copy_if_missing "$OLD_STATE_PATH" "$REPO_DIR/var/data/state.json"
copy_if_missing "$OLD_PLAN_PATH" "$REPO_DIR/var/data/plan.json"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  sudo -u "$SERVICE_USER" python3 -m venv "$VENV_DIR"
fi
if [[ -f "$REQUIREMENTS_FILE" ]]; then
  sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install -r "$REQUIREMENTS_FILE"
fi

cat > "$ENV_FILE" <<EOF
NETCONTROL_BIN_DIR=/opt/deviceportal/bin
CONFIG_PATH=$REPO_DIR/var/data/config.json
STORAGE_CONFIG_PATH=$REPO_DIR/var/data/config-storage.json
DEVICE_PATH=$REPO_DIR/var/data/device.json
FINGERPRINT_PATH=$REPO_DIR/var/data/fingerprint.json
STATE_PATH=$REPO_DIR/var/data/state.json
PLAN_PATH=$REPO_DIR/var/data/plan.json
ASSET_DIR=$REPO_DIR/var/assets
EOF
chmod 0644 "$ENV_FILE"

cat > "$SERVICE_FILE_DST" <<EOF
[Unit]
Description=Joormann-Media DevicePortal (Flask)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$REPO_DIR
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-$ENV_FILE
ExecStart=$VENV_DIR/bin/python -m app.main
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable device-portal.service

if [[ -x "$INTERNAL_STORAGE_SETUP" ]]; then
  if ! "$INTERNAL_STORAGE_SETUP" "$SERVICE_USER" "$SERVICE_GROUP"; then
    echo "WARN: internal storage setup failed; portal continues without blocking startup." >&2
  fi
fi

systemctl restart device-portal.service

echo "Installed systemd unit: $SERVICE_FILE_DST"
echo "Installed environment file: $ENV_FILE"
echo "Portal base setup done."
echo "Next: sudo ./install/setup_netcontrol.sh"
