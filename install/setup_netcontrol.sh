#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo ./install/setup_netcontrol.sh" >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_REPO_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
REPO_DIR="${1:-$DEFAULT_REPO_DIR}"
SERVICE_USER="${2:-www-data}"
SRC_DIR="$REPO_DIR/scripts/net"
DST_DIR="/opt/deviceportal/bin"
SUDOERS_FILE="/etc/sudoers.d/deviceportal-net"

if [[ ! -d "$SRC_DIR" ]]; then
  echo "Netcontrol source directory not found: $SRC_DIR" >&2
  echo "Usage: sudo ./install/setup_netcontrol.sh [REPO_DIR] [SERVICE_USER]" >&2
  exit 2
fi

apt-get update
apt-get install -y network-manager rfkill bluez iproute2 isc-dhcp-client pamtester

install -d -m 0755 "$DST_DIR"
install -m 0750 "$SRC_DIR/wifi_toggle.sh" "$DST_DIR/wifi_toggle.sh"
install -m 0750 "$SRC_DIR/wifi_profile.sh" "$DST_DIR/wifi_profile.sh"
install -m 0750 "$SRC_DIR/wifi_status.sh" "$DST_DIR/wifi_status.sh"
install -m 0750 "$SRC_DIR/wifi_disconnect.sh" "$DST_DIR/wifi_disconnect.sh"
install -m 0750 "$SRC_DIR/wifi_dhcp.sh" "$DST_DIR/wifi_dhcp.sh"
install -m 0750 "$SRC_DIR/bluetooth_toggle.sh" "$DST_DIR/bluetooth_toggle.sh"
install -m 0750 "$SRC_DIR/bluetooth_ctl.sh" "$DST_DIR/bluetooth_ctl.sh"
install -m 0750 "$SRC_DIR/bluetooth_pairing_feedback.sh" "$DST_DIR/bluetooth_pairing_feedback.sh"
install -m 0750 "$SRC_DIR/bluetooth_pairing_session.sh" "$DST_DIR/bluetooth_pairing_session.sh"
install -m 0750 "$SRC_DIR/bluetooth_pairing_action.sh" "$DST_DIR/bluetooth_pairing_action.sh"
install -m 0750 "$SRC_DIR/bluetooth_paired_devices.sh" "$DST_DIR/bluetooth_paired_devices.sh"
install -m 0755 "$SRC_DIR/bluetooth_audio.py" "$DST_DIR/bluetooth_audio.py"
install -m 0755 "$SRC_DIR/audio_output_ctl.py" "$DST_DIR/audio_output_ctl.py"
install -m 0755 "$SRC_DIR/audio_volume_ctl.py" "$DST_DIR/audio_volume_ctl.py"
install -m 0750 "$SRC_DIR/lan_toggle.sh" "$DST_DIR/lan_toggle.sh"
install -m 0750 "$SRC_DIR/wps_start.sh" "$DST_DIR/wps_start.sh"
install -m 0750 "$SRC_DIR/ap_enable.sh" "$DST_DIR/ap_enable.sh"
install -m 0750 "$SRC_DIR/ap_disable.sh" "$DST_DIR/ap_disable.sh"
install -m 0750 "$SRC_DIR/ap_status.sh" "$DST_DIR/ap_status.sh"
install -m 0750 "$SRC_DIR/ap_clients.sh" "$DST_DIR/ap_clients.sh"
install -m 0755 "$SRC_DIR/storage_probe.sh" "$DST_DIR/storage_probe.sh"
install -m 0750 "$SRC_DIR/storage_mount.sh" "$DST_DIR/storage_mount.sh"
install -m 0750 "$SRC_DIR/storage_internal_mount.sh" "$DST_DIR/storage_internal_mount.sh"
install -m 0750 "$SRC_DIR/storage_format.sh" "$DST_DIR/storage_format.sh"
install -m 0750 "$SRC_DIR/storage_unmount.sh" "$DST_DIR/storage_unmount.sh"
install -m 0750 "$SRC_DIR/portal_update.sh" "$DST_DIR/portal_update.sh"
install -m 0750 "$SRC_DIR/portal_service_install.sh" "$DST_DIR/portal_service_install.sh"
install -m 0750 "$SRC_DIR/player_update.sh" "$DST_DIR/player_update.sh"
install -m 0750 "$SRC_DIR/player_service.sh" "$DST_DIR/player_service.sh"
install -m 0750 "$SRC_DIR/spotify_connect_service.sh" "$DST_DIR/spotify_connect_service.sh"
install -m 0750 "$SRC_DIR/spotify_connect_install.sh" "$DST_DIR/spotify_connect_install.sh"
install -m 0750 "$SRC_DIR/tailscale_dns_fix.sh" "$DST_DIR/tailscale_dns_fix.sh"
install -m 0750 "$SRC_DIR/hostname_rename.sh" "$DST_DIR/hostname_rename.sh"
install -m 0750 "$SRC_DIR/local_auth.sh" "$DST_DIR/local_auth.sh"
install -m 0755 "$SRC_DIR/network_info.sh" "$DST_DIR/network_info.sh"

if getent group netdev >/dev/null 2>&1; then
  usermod -aG netdev "$SERVICE_USER" || true
fi
for grp in video render input audio; do
  if getent group "${grp}" >/dev/null 2>&1; then
    usermod -aG "${grp}" "$SERVICE_USER" || true
  fi
done

cat > "$SUDOERS_FILE" <<SUDO
Defaults:${SERVICE_USER} !requiretty
${SERVICE_USER} ALL=(root) NOPASSWD: ${DST_DIR}/wifi_toggle.sh *, ${DST_DIR}/wifi_profile.sh *, ${DST_DIR}/wifi_status.sh *, ${DST_DIR}/wifi_disconnect.sh *, ${DST_DIR}/wifi_dhcp.sh *, ${DST_DIR}/bluetooth_toggle.sh *, ${DST_DIR}/bluetooth_ctl.sh *, ${DST_DIR}/bluetooth_pairing_feedback.sh *, ${DST_DIR}/bluetooth_pairing_session.sh *, ${DST_DIR}/bluetooth_pairing_action.sh *, ${DST_DIR}/bluetooth_paired_devices.sh *, ${DST_DIR}/bluetooth_audio.py *, ${DST_DIR}/audio_output_ctl.py *, ${DST_DIR}/lan_toggle.sh *, ${DST_DIR}/wps_start.sh *, ${DST_DIR}/ap_enable.sh *, ${DST_DIR}/ap_disable.sh *, ${DST_DIR}/ap_status.sh *, ${DST_DIR}/ap_clients.sh *, ${DST_DIR}/storage_mount.sh *, ${DST_DIR}/storage_internal_mount.sh, ${DST_DIR}/storage_format.sh *, ${DST_DIR}/storage_unmount.sh *, ${DST_DIR}/portal_update.sh *, ${DST_DIR}/portal_service_install.sh *, ${DST_DIR}/player_update.sh *, ${DST_DIR}/player_service.sh *, ${DST_DIR}/spotify_connect_service.sh *, ${DST_DIR}/spotify_connect_install.sh *, ${DST_DIR}/tailscale_dns_fix.sh *, ${DST_DIR}/hostname_rename.sh *, ${DST_DIR}/local_auth.sh *
SUDO

chmod 0440 "$SUDOERS_FILE"
visudo -cf "$SUDOERS_FILE"

echo "Netcontrol deployed to $DST_DIR"
echo "sudoers installed at $SUDOERS_FILE for user ${SERVICE_USER}"
echo "Set NETCONTROL_BIN_DIR=$DST_DIR in /etc/default/jm-deviceportal if needed."
