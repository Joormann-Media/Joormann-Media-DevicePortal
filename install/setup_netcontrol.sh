#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo ./install/setup_netcontrol.sh" >&2
  exit 1
fi

REPO_DIR="${1:-/opt/jm-deviceportal}"
SERVICE_USER="${2:-www-data}"
SRC_DIR="$REPO_DIR/scripts/net"
DST_DIR="/opt/deviceportal/bin"
SUDOERS_FILE="/etc/sudoers.d/deviceportal-net"

apt-get update
apt-get install -y network-manager rfkill bluez iproute2

install -d -m 0755 "$DST_DIR"
install -m 0750 "$SRC_DIR/wifi_toggle.sh" "$DST_DIR/wifi_toggle.sh"
install -m 0750 "$SRC_DIR/bluetooth_toggle.sh" "$DST_DIR/bluetooth_toggle.sh"
install -m 0750 "$SRC_DIR/lan_toggle.sh" "$DST_DIR/lan_toggle.sh"
install -m 0750 "$SRC_DIR/wps_start.sh" "$DST_DIR/wps_start.sh"
install -m 0755 "$SRC_DIR/network_info.sh" "$DST_DIR/network_info.sh"

cat > "$SUDOERS_FILE" <<SUDO
Defaults:${SERVICE_USER} !requiretty
${SERVICE_USER} ALL=(root) NOPASSWD: ${DST_DIR}/wifi_toggle.sh *, ${DST_DIR}/bluetooth_toggle.sh *, ${DST_DIR}/lan_toggle.sh *, ${DST_DIR}/wps_start.sh *
SUDO

chmod 0440 "$SUDOERS_FILE"
visudo -cf "$SUDOERS_FILE"

echo "Netcontrol deployed to $DST_DIR"
echo "sudoers installed at $SUDOERS_FILE for user ${SERVICE_USER}"
echo "Set NETCONTROL_BIN_DIR=$DST_DIR in /etc/default/jm-deviceportal if needed."
