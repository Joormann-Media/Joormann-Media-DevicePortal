#!/usr/bin/env bash
set -euo pipefail

LAN_IF="${1:-eth0}"
WIFI_IF="${2:-wlan0}"

cmd_present() {
  command -v "$1" >/dev/null 2>&1 && echo "1" || echo "0"
}

safe_cmd() {
  "$@" 2>/dev/null || true
}

iface_exists() {
  [[ -d "/sys/class/net/$1" ]] && echo "1" || echo "0"
}

iface_enabled() {
  local ifname="$1"
  if [[ ! -d "/sys/class/net/$ifname" ]]; then
    echo "0"
    return
  fi
  local state
  state="$(cat "/sys/class/net/$ifname/operstate" 2>/dev/null || echo "down")"
  [[ "$state" == "up" || "$state" == "unknown" ]] && echo "1" || echo "0"
}

iface_carrier() {
  local ifname="$1"
  [[ -f "/sys/class/net/$ifname/carrier" ]] && cat "/sys/class/net/$ifname/carrier" 2>/dev/null || echo "0"
}

iface_mac() {
  local ifname="$1"
  [[ -f "/sys/class/net/$ifname/address" ]] && cat "/sys/class/net/$ifname/address" 2>/dev/null || echo ""
}

iface_ip() {
  local ifname="$1"
  safe_cmd ip -4 -o addr show dev "$ifname" | awk '{print $4}' | cut -d/ -f1 | head -n1
}

HOSTNAME_VAL="$(hostname 2>/dev/null || echo "unknown")"
NMCLI_PRESENT="$(cmd_present nmcli)"
RFKILL_PRESENT="$(cmd_present rfkill)"
TAILSCALE_PRESENT="$(cmd_present tailscale)"

LAN_EXISTS="$(iface_exists "$LAN_IF")"
LAN_ENABLED="$(iface_enabled "$LAN_IF")"
LAN_CARRIER="$(iface_carrier "$LAN_IF")"
LAN_IP="$(iface_ip "$LAN_IF")"
LAN_MAC="$(iface_mac "$LAN_IF")"

WIFI_EXISTS="$(iface_exists "$WIFI_IF")"
WIFI_ENABLED="$(iface_enabled "$WIFI_IF")"
WIFI_IP="$(iface_ip "$WIFI_IF")"
WIFI_MAC="$(iface_mac "$WIFI_IF")"
WIFI_CONNECTED="0"
WIFI_SSID=""
WIFI_SIGNAL=""
WIFI_RADIO="unknown"

if [[ "$NMCLI_PRESENT" == "1" ]]; then
  WIFI_RADIO="$(safe_cmd nmcli -t -f WIFI general | head -n1)"
  if [[ "$WIFI_RADIO" == "enabled" ]]; then
    WIFI_ENABLED="1"
  elif [[ "$WIFI_RADIO" == "disabled" ]]; then
    WIFI_ENABLED="0"
  fi

  WIFI_LINE="$(safe_cmd nmcli -t -f ACTIVE,SSID,SIGNAL dev wifi | awk -F: '$1=="yes"||$1=="*" {print; exit}')"
  if [[ -n "$WIFI_LINE" ]]; then
    WIFI_CONNECTED="1"
    WIFI_SSID="$(echo "$WIFI_LINE" | cut -d: -f2)"
    WIFI_SIGNAL="$(echo "$WIFI_LINE" | cut -d: -f3)"
  fi
fi

BT_ENABLED="0"
if [[ "$RFKILL_PRESENT" == "1" ]]; then
  BT_LINES="$(safe_cmd rfkill list bluetooth)"
  if [[ -n "$BT_LINES" ]]; then
    SOFT_BLOCKED="$(echo "$BT_LINES" | awk -F': ' '/Soft blocked/ {print $2; exit}')"
    HARD_BLOCKED="$(echo "$BT_LINES" | awk -F': ' '/Hard blocked/ {print $2; exit}')"
    if [[ "$SOFT_BLOCKED" == "no" && "$HARD_BLOCKED" == "no" ]]; then
      BT_ENABLED="1"
    fi
  fi
fi

GATEWAY="$(safe_cmd ip route | awk '/default/ {print $3; exit}')"
DNS_RAW="$(safe_cmd awk '/^nameserver / {print $2}' /etc/resolv.conf | paste -sd, -)"
TAILSCALE_IP=""
if [[ "$TAILSCALE_PRESENT" == "1" ]]; then
  TAILSCALE_IP="$(safe_cmd tailscale ip -4 | head -n1)"
fi

export HOSTNAME_VAL LAN_IF WIFI_IF
export LAN_ENABLED LAN_CARRIER LAN_IP LAN_MAC LAN_EXISTS
export WIFI_ENABLED WIFI_CONNECTED WIFI_SSID WIFI_SIGNAL WIFI_IP WIFI_MAC WIFI_EXISTS WIFI_RADIO
export BT_ENABLED
export GATEWAY DNS_RAW
export NMCLI_PRESENT RFKILL_PRESENT TAILSCALE_PRESENT TAILSCALE_IP

python3 - <<'PY'
import json
import os


def b(name: str) -> bool:
    return os.getenv(name, "0") in ("1", "true", "True", "yes")


def s(name: str) -> str:
    return (os.getenv(name, "") or "").strip()


def maybe_int(value: str):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except Exception:
        return None


dns = [x for x in s("DNS_RAW").split(",") if x]

payload = {
    "hostname": s("HOSTNAME_VAL"),
    "interfaces": {
        "lan": {
            "ifname": s("LAN_IF"),
            "present": b("LAN_EXISTS"),
            "enabled": b("LAN_ENABLED"),
            "carrier": b("LAN_CARRIER"),
            "ip": s("LAN_IP"),
            "mac": s("LAN_MAC"),
        },
        "wifi": {
            "ifname": s("WIFI_IF"),
            "present": b("WIFI_EXISTS"),
            "enabled": b("WIFI_ENABLED"),
            "connected": b("WIFI_CONNECTED"),
            "ssid": s("WIFI_SSID"),
            "signal": maybe_int(s("WIFI_SIGNAL")),
            "ip": s("WIFI_IP"),
            "mac": s("WIFI_MAC"),
            "radio": s("WIFI_RADIO"),
        },
        "bluetooth": {
            "present": b("RFKILL_PRESENT"),
            "enabled": b("BT_ENABLED"),
        },
    },
    "routes": {
        "gateway": s("GATEWAY"),
        "dns": dns,
    },
    "tailscale": {
        "present": b("TAILSCALE_PRESENT"),
        "ip": s("TAILSCALE_IP"),
    },
    "tools": {
        "nmcli": b("NMCLI_PRESENT"),
        "rfkill": b("RFKILL_PRESENT"),
        "tailscale": b("TAILSCALE_PRESENT"),
    },
}

print(json.dumps(payload, ensure_ascii=False))
PY
