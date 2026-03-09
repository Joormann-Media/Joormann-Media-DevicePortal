from __future__ import annotations

from app.core.netcontrol import NetControlError, get_network_info


def has_uplink(network_info: dict) -> bool:
    interfaces = (network_info or {}).get("interfaces") or {}
    lan = interfaces.get("lan") or {}
    wifi = interfaces.get("wifi") or {}

    lan_up = bool(lan.get("carrier")) or bool(str(lan.get("ip") or "").strip())
    wifi_up = bool(wifi.get("connected"))
    return bool(lan_up or wifi_up)


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
