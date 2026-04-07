#!/usr/bin/env bash
set -euo pipefail
sudo systemctl stop device-portal.service || true
sudo systemctl disable device-portal.service || true
sudo rm -f /etc/systemd/system/device-portal.service
sudo systemctl daemon-reload
sudo systemctl reset-failed device-portal.service || true
echo "Service entfernt: device-portal.service"
