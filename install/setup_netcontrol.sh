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
SUDOERS_FILE="/etc/sudoers.d/deviceportal-netcontrol"
LEGACY_SUDOERS_FILE="/etc/sudoers.d/deviceportal-net"

if [[ ! -d "$SRC_DIR" ]]; then
  echo "Netcontrol source directory not found: $SRC_DIR" >&2
  echo "Usage: sudo ./install/setup_netcontrol.sh [REPO_DIR] [SERVICE_USER]" >&2
  exit 2
fi

export DEBIAN_FRONTEND=noninteractive
if ! apt-get update; then
  echo "WARN: apt-get update returned errors (likely 3rd-party repos). Continuing with available package indexes." >&2
fi

pkg_available() {
  local pkg="$1"
  apt-cache show "$pkg" >/dev/null 2>&1
}

BASE_PKGS=(
  network-manager
  rfkill
  bluez
  iproute2
  isc-dhcp-client
  pamtester
  ntfs-3g
  exfatprogs
  pulseaudio-utils
  pipewire-bin
  pipewire-pulse
  pipewire-alsa
  wireplumber
  alsa-utils
  mpv
  espeak
  libttspico-utils
)

# Distro-dependent audio plugin package names.
OPTIONAL_PKGS=()
for candidate in libspa-0.2-modules libspa-0.2-alsa pipewire-audio-client-libraries; do
  if pkg_available "$candidate"; then
    OPTIONAL_PKGS+=("$candidate")
  fi
done

INSTALL_PKGS=("${BASE_PKGS[@]}" "${OPTIONAL_PKGS[@]}")

if ! apt-get install -y "${INSTALL_PKGS[@]}"; then
  echo "WARN: apt-get install failed on first try, retrying with --fix-missing." >&2
  apt-get install -y --fix-missing "${INSTALL_PKGS[@]}"
fi

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
install -m 0750 "$SRC_DIR/portal_restart.sh" "$DST_DIR/portal_restart.sh"
install -m 0750 "$SRC_DIR/portal_service_install.sh" "$DST_DIR/portal_service_install.sh"
install -m 0750 "$SRC_DIR/player_update.sh" "$DST_DIR/player_update.sh"
install -m 0750 "$SRC_DIR/player_service.sh" "$DST_DIR/player_service.sh"
install -m 0750 "$SRC_DIR/player_service_install.sh" "$DST_DIR/player_service_install.sh"
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

# Ensure user-level audio stack can run even without an active desktop login.
if command -v loginctl >/dev/null 2>&1; then
  loginctl enable-linger "$SERVICE_USER" >/dev/null 2>&1 || true
fi

if command -v systemctl >/dev/null 2>&1; then
  SERVICE_UID="$(id -u "$SERVICE_USER" 2>/dev/null || echo "")"
  if [[ -n "${SERVICE_UID}" ]]; then
    USER_RUNTIME_DIR="/run/user/${SERVICE_UID}"
    runuser -u "$SERVICE_USER" -- env \
      XDG_RUNTIME_DIR="${USER_RUNTIME_DIR}" \
      DBUS_SESSION_BUS_ADDRESS="unix:path=${USER_RUNTIME_DIR}/bus" \
      systemctl --user daemon-reload >/dev/null 2>&1 || true
    runuser -u "$SERVICE_USER" -- env \
      XDG_RUNTIME_DIR="${USER_RUNTIME_DIR}" \
      DBUS_SESSION_BUS_ADDRESS="unix:path=${USER_RUNTIME_DIR}/bus" \
      systemctl --user enable --now wireplumber.service pipewire.service pipewire-pulse.service >/dev/null 2>&1 || true
  fi
fi

cat > "$SUDOERS_FILE" <<SUDO
Defaults:${SERVICE_USER} !requiretty
${SERVICE_USER} ALL=(root) NOPASSWD: ${DST_DIR}/wifi_toggle.sh *, ${DST_DIR}/wifi_profile.sh *, ${DST_DIR}/wifi_status.sh *, ${DST_DIR}/wifi_disconnect.sh *, ${DST_DIR}/wifi_dhcp.sh *, ${DST_DIR}/bluetooth_toggle.sh *, ${DST_DIR}/bluetooth_ctl.sh *, ${DST_DIR}/bluetooth_pairing_feedback.sh *, ${DST_DIR}/bluetooth_pairing_session.sh *, ${DST_DIR}/bluetooth_pairing_action.sh *, ${DST_DIR}/bluetooth_paired_devices.sh *, ${DST_DIR}/bluetooth_audio.py *, ${DST_DIR}/audio_output_ctl.py *, ${DST_DIR}/lan_toggle.sh *, ${DST_DIR}/wps_start.sh *, ${DST_DIR}/ap_enable.sh *, ${DST_DIR}/ap_disable.sh *, ${DST_DIR}/ap_status.sh *, ${DST_DIR}/ap_clients.sh *, ${DST_DIR}/storage_mount.sh *, ${DST_DIR}/storage_internal_mount.sh, ${DST_DIR}/storage_format.sh *, ${DST_DIR}/storage_unmount.sh *, ${DST_DIR}/portal_update.sh *, ${DST_DIR}/portal_restart.sh *, ${DST_DIR}/portal_service_install.sh *, ${DST_DIR}/player_update.sh *, ${DST_DIR}/player_service.sh *, ${DST_DIR}/player_service_install.sh *, ${DST_DIR}/spotify_connect_service.sh *, ${DST_DIR}/spotify_connect_install.sh *, ${DST_DIR}/tailscale_dns_fix.sh *, ${DST_DIR}/hostname_rename.sh *, ${DST_DIR}/local_auth.sh *
SUDO

chmod 0440 "$SUDOERS_FILE"
visudo -cf "$SUDOERS_FILE"
if [[ -f "$LEGACY_SUDOERS_FILE" && "$LEGACY_SUDOERS_FILE" != "$SUDOERS_FILE" ]]; then
  rm -f "$LEGACY_SUDOERS_FILE"
fi

echo "Netcontrol deployed to $DST_DIR"
echo "sudoers installed at $SUDOERS_FILE for user ${SERVICE_USER}"
echo "Set NETCONTROL_BIN_DIR=$DST_DIR in /etc/default/jm-deviceportal if needed."
