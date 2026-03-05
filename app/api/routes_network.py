from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.core.netcontrol import (
    NetControlError,
    get_network_info,
    set_bluetooth_enabled,
    set_lan_enabled,
    set_wifi_enabled,
    start_wps,
)

bp_network = Blueprint("network", __name__)


def _ok(data: dict, status: int = 200):
    return jsonify(ok=True, data=data), status


def _error(code: str, message: str, status: int = 400, detail: str = ""):
    payload = {"code": code, "message": message}
    if detail:
        payload["detail"] = detail
    return jsonify(ok=False, error=payload), status


@bp_network.get("/api/network/info")
def api_network_info():
    try:
        info = get_network_info()
        return _ok(info)
    except NetControlError as exc:
        http_status = 500 if exc.code in ("script_missing", "execution_failed") else 400
        return _error(exc.code, exc.message, status=http_status, detail=exc.detail)


@bp_network.post("/api/network/wifi/toggle")
def api_network_wifi_toggle():
    data = request.get_json(force=True, silent=True) or {}
    if "enabled" not in data or not isinstance(data.get("enabled"), bool):
        return _error("invalid_payload", "Field 'enabled' (bool) is required", status=400)
    try:
        result = set_wifi_enabled(bool(data["enabled"]))
        return _ok(result)
    except NetControlError as exc:
        status = 500 if exc.code == "script_missing" else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.post("/api/network/bluetooth/toggle")
def api_network_bluetooth_toggle():
    data = request.get_json(force=True, silent=True) or {}
    if "enabled" not in data or not isinstance(data.get("enabled"), bool):
        return _error("invalid_payload", "Field 'enabled' (bool) is required", status=400)
    try:
        result = set_bluetooth_enabled(bool(data["enabled"]))
        return _ok(result)
    except NetControlError as exc:
        status = 500 if exc.code == "script_missing" else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.post("/api/network/lan/toggle")
def api_network_lan_toggle():
    data = request.get_json(force=True, silent=True) or {}
    if "enabled" not in data or not isinstance(data.get("enabled"), bool):
        return _error("invalid_payload", "Field 'enabled' (bool) is required", status=400)
    ifname = (data.get("ifname") or "eth0").strip()
    try:
        result = set_lan_enabled(bool(data["enabled"]), ifname=ifname)
        return _ok(result)
    except NetControlError as exc:
        status = 500 if exc.code == "script_missing" else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.post("/api/network/wps")
def api_network_wps():
    data = request.get_json(force=True, silent=True) or {}
    ifname = (data.get("ifname") or "wlan0").strip()
    try:
        result = start_wps(ifname=ifname)
        return (
            jsonify(
                ok=True,
                success=True,
                message=result.get(
                    "message",
                    "WPS wurde gestartet. Bitte jetzt innerhalb von 2 Minuten am Router die WPS-Taste druecken.",
                ),
                details=result.get("details", ""),
                hint=result.get("hint", "Je nach Router kann die Verbindung 30-120 Sekunden dauern."),
                data={"iface": result.get("ifname", ifname), "code": result.get("code", "ok")},
            ),
            200,
        )
    except NetControlError as exc:
        status = 400 if exc.code in ("invalid_interface", "wifi_interface_missing") else 500
        return (
            jsonify(
                ok=False,
                success=False,
                message=exc.message,
                details=exc.detail,
                hint="Pruefe WLAN-Adapter, NetworkManager und druecke danach die WPS-Taste am Router.",
                data={"iface": ifname, "code": exc.code},
                error={"code": exc.code, "message": exc.message, "detail": exc.detail},
            ),
            status,
        )
