from __future__ import annotations

from app.core.netcontrol import NetControlError, get_network_info


def _has_non_local_ip(value: str | None) -> bool:
    ip = str(value or "").strip()
    if not ip:
        return False
    if ip.startswith("127."):
        return False
    if ip == "0.0.0.0":
        return False
    return True


def has_uplink(network_info: dict) -> bool:
    interfaces = (network_info or {}).get("interfaces") or {}
    lan = interfaces.get("lan") or {}
    wifi = interfaces.get("wifi") or {}
    routes = (network_info or {}).get("routes") or {}
    tailscale = (network_info or {}).get("tailscale") or {}

    lan_up = bool(lan.get("carrier")) or _has_non_local_ip(lan.get("ip"))
    wifi_up = bool(wifi.get("connected")) or _has_non_local_ip(wifi.get("ip"))
    default_route_up = bool(str(routes.get("gateway") or "").strip())
    tailscale_up = _has_non_local_ip(tailscale.get("ip"))

    any_interface_ip_up = False
    for entry in interfaces.values():
        if not isinstance(entry, dict):
            continue
        if _has_non_local_ip(entry.get("ip")) or _has_non_local_ip(entry.get("ipv4")):
            any_interface_ip_up = True
            break

    return bool(lan_up or wifi_up or default_route_up or tailscale_up or any_interface_ip_up)


def detect_connectivity_setup_mode() -> dict:
    try:
        info = get_network_info()
    except NetControlError:
        return {
            "active": False,
            "reason": "network_info_unavailable",
            "message": "Network status unavailable",
        }

    active = not has_uplink(info)
    return {
        "active": active,
        "reason": "missing_lan_and_wifi_uplink" if active else "uplink_available",
        "message": (
            "Kein LAN/WLAN-Uplink erkannt. Setup-Modus aktiv, lokaler Login erzwungen."
            if active
            else "Uplink verfügbar."
        ),
    }
