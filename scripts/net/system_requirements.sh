#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-}"
KEY="${2:-}"
REPO_DIR="${3:-}"
SERVICE_USER="${4:-}"

if [[ -z "$ACTION" || -z "$KEY" ]]; then
  echo "code=invalid_arguments"
  echo "message=Action and key are required"
  exit 2
fi

declare -A PACKAGE_BY_KEY=(
  [git]="git"
  [python3]="python3"
  [python3_venv]="python3-venv"
  [pip]="python3-pip"
  [nginx]="nginx"
  [curl]="curl"
  [ca_certificates]="ca-certificates"
  [avahi]="avahi-daemon"
  [networkmanager]="network-manager"
  [bluez]="bluez"
  [pamtester]="pamtester"
)

declare -A SERVICE_BY_KEY=(
  [nginx]="nginx"
  [avahi]="avahi-daemon"
  [networkmanager]="NetworkManager"
  [bluez]="bluetooth"
  [tailscale]="tailscaled"
)

pkg="${PACKAGE_BY_KEY[$KEY]:-}"
svc="${SERVICE_BY_KEY[$KEY]:-}"

if [[ -z "$REPO_DIR" ]]; then
  if [[ -d "/opt/jm-deviceportal/install" ]]; then
    REPO_DIR="/opt/jm-deviceportal"
  else
    SELF_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    REPO_DIR="$(cd -- "$SELF_DIR/../.." && pwd)"
  fi
fi

if [[ -z "$SERVICE_USER" ]]; then
  SERVICE_USER="$(systemctl show device-portal.service --property=User --value 2>/dev/null || true)"
  if [[ -z "$SERVICE_USER" ]]; then
    SERVICE_USER="www-data"
  fi
fi

install_tailscale() {
  if command -v tailscale >/dev/null 2>&1; then
    return 0
  fi
  export DEBIAN_FRONTEND=noninteractive
  curl -fsSL https://tailscale.com/install.sh | sh
}

install_netcontrol_scripts() {
  if [[ ! -x "$REPO_DIR/install/setup_netcontrol.sh" ]]; then
    echo "code=setup_netcontrol_missing"
    echo "message=Installer missing: $REPO_DIR/install/setup_netcontrol.sh"
    exit 7
  fi
  bash -lc "cd \"$REPO_DIR\" && ./install/setup_netcontrol.sh \"$REPO_DIR\" \"$SERVICE_USER\""
}

case "$ACTION" in
  install)
    if [[ "$KEY" == "tailscale" ]]; then
      install_tailscale
      echo "code=ok"
      echo "action=install"
      echo "key=$KEY"
      echo "package=tailscale"
      echo "message=Package installed"
      exit 0
    fi
    if [[ "$KEY" == "netcontrol_scripts" || "$KEY" == "local_auth_script" || "$KEY" == "local_auth_sudoers" ]]; then
      install_netcontrol_scripts
      echo "code=ok"
      echo "action=install"
      echo "key=$KEY"
      echo "package=netcontrol_scripts"
      echo "message=NetControl scripts installed"
      exit 0
    fi
    if [[ -z "$pkg" ]]; then
      echo "code=install_not_supported"
      echo "message=Install not supported for key: $KEY"
      exit 3
    fi
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y >/dev/null
    apt-get install -y "$pkg"
    echo "code=ok"
    echo "action=install"
    echo "key=$KEY"
    echo "package=$pkg"
    echo "message=Package installed"
    ;;
  uninstall)
    if [[ "$KEY" == "netcontrol_scripts" || "$KEY" == "local_auth_script" || "$KEY" == "local_auth_sudoers" ]]; then
      echo "code=uninstall_not_supported"
      echo "message=Uninstall not supported for key: $KEY"
      exit 6
    fi
    if [[ -z "$pkg" ]]; then
      echo "code=uninstall_not_supported"
      echo "message=Uninstall not supported for key: $KEY"
      exit 6
    fi
    if [[ -n "$svc" ]]; then
      systemctl stop "$svc" >/dev/null 2>&1 || true
    fi
    export DEBIAN_FRONTEND=noninteractive
    apt-get remove -y "$pkg"
    echo "code=ok"
    echo "action=uninstall"
    echo "key=$KEY"
    echo "package=$pkg"
    echo "message=Package removed"
    ;;
  start)
    if [[ -z "$svc" ]]; then
      echo "code=start_not_supported"
      echo "message=Start not supported for key: $KEY"
      exit 4
    fi
    systemctl start "$svc"
    state="$(systemctl is-active "$svc" 2>/dev/null || true)"
    echo "code=ok"
    echo "action=start"
    echo "key=$KEY"
    echo "service=$svc"
    echo "runtime=${state:-unknown}"
    echo "message=Service start triggered"
    ;;
  *)
    echo "code=invalid_action"
    echo "message=Invalid action: $ACTION"
    exit 5
    ;;
esac
