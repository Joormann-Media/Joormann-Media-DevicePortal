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

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "Service user does not exist: $SERVICE_USER" >&2
  echo "Usage: sudo ./install/setup_portal.sh [REPO_DIR] [SERVICE_USER]" >&2
  exit 2
fi

if [[ ! -d "$REPO_DIR" ]]; then
  echo "Repository directory not found: $REPO_DIR" >&2
  exit 2
fi

apt-get update
apt-get install -y python3 python3-venv python3-pip

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

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  sudo -u "$SERVICE_USER" python3 -m venv "$VENV_DIR"
fi
if [[ -f "$REQUIREMENTS_FILE" ]]; then
  sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install -r "$REQUIREMENTS_FILE"
fi

cat > "$ENV_FILE" <<EOF
NETCONTROL_BIN_DIR=/opt/deviceportal/bin
CONFIG_PATH=$REPO_DIR/var/data/config.json
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
systemctl restart device-portal.service
echo "Installed systemd unit: $SERVICE_FILE_DST"
echo "Installed environment file: $ENV_FILE"
echo "Portal base setup done."
echo "Next: sudo ./install/setup_netcontrol.sh"
