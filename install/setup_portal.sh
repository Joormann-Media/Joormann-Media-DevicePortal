#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo ./install/setup_portal.sh" >&2
  exit 1
fi

REPO_DIR="${1:-/opt/jm-deviceportal}"
SERVICE_USER="${2:-www-data}"
SERVICE_FILE_SRC="$REPO_DIR/docs/systemd/device-portal.service"
SERVICE_FILE_DST="/etc/systemd/system/device-portal.service"

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

if [[ -f "$SERVICE_FILE_SRC" ]]; then
  cp "$SERVICE_FILE_SRC" "$SERVICE_FILE_DST"
  systemctl daemon-reload
  systemctl enable device-portal.service
  echo "Installed systemd unit: $SERVICE_FILE_DST"
else
  echo "Warning: systemd template not found at $SERVICE_FILE_SRC"
fi

echo "Portal base setup done."
echo "Next: sudo ./install/setup_netcontrol.sh"
