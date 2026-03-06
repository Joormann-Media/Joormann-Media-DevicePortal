from __future__ import annotations

import time

from flask import Blueprint, jsonify, request

from app.core.config import ensure_config
from app.core.jsonio import write_json
from app.core.netcontrol import (
    NONPREFERRED_WIFI_PRIORITY,
    PREFERRED_WIFI_PRIORITY,
    NetControlError,
    disable_tailscale_dns_override,
    get_network_info,
    set_bluetooth_enabled,
    set_lan_enabled,
    set_wifi_enabled,
    start_wps,
    wifi_connect,
    wifi_profile_delete,
    wifi_profile_set,
    wifi_profile_up,
    wifi_profiles_list,
    wifi_scan,
)
from app.core.paths import CONFIG_PATH
from app.core.timeutil import utc_now

bp_network = Blueprint("network", __name__)


def _ok(data: dict, status: int = 200):
    return jsonify(ok=True, data=data), status


def _error(code: str, message: str, status: int = 400, detail: str = ""):
    payload = {"code": code, "message": message}
    if detail:
        payload["detail"] = detail
    return jsonify(ok=False, error=payload), status


def _norm_profiles(cfg: dict) -> list[dict]:
    profs = cfg.get("wifi_profiles")
    if not isinstance(profs, list):
        return []
    clean: list[dict] = []
    for item in profs:
        if not isinstance(item, dict):
            continue
        ssid = (item.get("ssid") or "").strip()
        if not ssid:
            continue
        try:
            prio = int(item.get("priority", 0))
        except Exception:
            prio = 0
        auto = bool(item.get("autoconnect", True))
        clean.append({"ssid": ssid, "priority": prio, "autoconnect": auto})
    unique: dict[str, dict] = {}
    for item in clean:
        unique[item["ssid"]] = item
    return list(unique.values())


def _save_wifi_profiles(cfg: dict, profiles: list[dict], preferred: str = "", last_ssid: str = "") -> tuple[bool, str]:
    cfg["wifi_profiles"] = profiles
    if preferred:
        cfg["preferred_wifi"] = preferred
    if last_ssid:
        cfg["last_wifi_ssid"] = last_ssid
    cfg["updated_at"] = utc_now()
    return write_json(CONFIG_PATH, cfg, mode=0o600)


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
    target_bssid = (data.get("target_bssid") or "").strip()
    target_ssid = (data.get("target_ssid") or "").strip()
    try:
        result = start_wps(ifname=ifname, target_bssid=target_bssid, target_ssid=target_ssid)
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
                data={
                    "iface": result.get("ifname", ifname),
                    "code": result.get("code", "ok"),
                    "network": result.get("network", {}),
                },
            ),
            200,
        )
    except NetControlError as exc:
        # WPS can still connect after trigger command return code; check live state before hard-failing.
        for _ in range(10):
            try:
                net_info = get_network_info()
                wifi = ((net_info or {}).get("interfaces") or {}).get("wifi") or {}
                if wifi.get("connected"):
                    return (
                        jsonify(
                            ok=True,
                            success=True,
                            message="WPS wurde gestartet und WLAN ist verbunden.",
                            details=exc.detail,
                            hint="Verbindung erkannt. Netzwerkinformationen wurden aktualisiert.",
                            data={
                                "iface": ifname,
                                "code": "connected_after_trigger_error",
                                "network": {
                                    "ssid": wifi.get("ssid", ""),
                                    "connection": wifi.get("connection", ""),
                                    "bssid": wifi.get("bssid", ""),
                                    "signal": wifi.get("signal", ""),
                                    "frequency_mhz": wifi.get("frequency_mhz", ""),
                                    "security": wifi.get("security", ""),
                                    "ip": wifi.get("ip", ""),
                                },
                            },
                        ),
                        200,
                    )
            except Exception:
                pass
            time.sleep(1)
        status = 400 if exc.code in ("invalid_interface", "wifi_interface_missing", "wps_timeout") else 500
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


@bp_network.get("/api/wifi/scan")
def api_wifi_scan():
    ifname = (request.args.get("ifname") or "wlan0").strip()
    try:
        return _ok(wifi_scan(ifname=ifname))
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.post("/api/wifi/connect")
def api_wifi_connect():
    data = request.get_json(force=True, silent=True) or {}
    ssid = (data.get("ssid") or "").strip()
    password = data.get("password") or ""
    ifname = (data.get("ifname") or "wlan0").strip()
    if not ssid:
        return _error("invalid_payload", "Field 'ssid' is required", status=400)
    try:
        result = wifi_connect(ssid=ssid, password=password, ifname=ifname)
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)

    cfg = ensure_config()
    profiles = _norm_profiles(cfg)
    found = False
    for item in profiles:
        if item["ssid"] == ssid:
            found = True
            break
    if not found:
        profiles.append({"ssid": ssid, "priority": NONPREFERRED_WIFI_PRIORITY, "autoconnect": True})
    ok, err = _save_wifi_profiles(cfg, profiles, last_ssid=ssid)
    if not ok:
        return _error("config_write_failed", "Connected Wi-Fi but failed to persist profile", status=500, detail=err)
    return _ok({**result, "persisted": True})


@bp_network.get("/api/wifi/profiles")
def api_wifi_profiles():
    cfg = ensure_config()
    profiles_cfg = _norm_profiles(cfg)
    try:
        nm_profiles = wifi_profiles_list().get("profiles", [])
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)
    nm_by_name = {item.get("name"): item for item in nm_profiles if item.get("name")}
    configured: list[dict] = []
    for item in profiles_cfg:
        ssid = item["ssid"]
        nm_item = nm_by_name.get(ssid)
        configured.append(
            {
                "ssid": ssid,
                "priority": item["priority"],
                "autoconnect": item["autoconnect"],
                "exists": bool(nm_item),
                "nm": nm_item,
            }
        )
    known = {item["ssid"] for item in profiles_cfg}
    unmanaged = [item for item in nm_profiles if item.get("name") not in known]
    return _ok(
        {
            "configured": configured,
            "unmanaged": unmanaged,
            "preferred_ssid": (cfg.get("preferred_wifi") or ""),
            "last_wifi_ssid": (cfg.get("last_wifi_ssid") or ""),
        }
    )


@bp_network.post("/api/wifi/profiles/add")
def api_wifi_profiles_add():
    data = request.get_json(force=True, silent=True) or {}
    ssid = (data.get("ssid") or "").strip()
    password = data.get("password") or ""
    ifname = (data.get("ifname") or "wlan0").strip()
    try:
        priority = int(data.get("priority") or NONPREFERRED_WIFI_PRIORITY)
    except Exception:
        priority = NONPREFERRED_WIFI_PRIORITY
    autoconnect = bool(data.get("autoconnect", True))
    if not ssid:
        return _error("invalid_payload", "Field 'ssid' is required", status=400)

    connect_error = ""
    try:
        wifi_connect(ssid=ssid, password=password, ifname=ifname)
    except NetControlError as exc:
        connect_error = exc.detail or exc.message

    try:
        wifi_profile_set(ssid=ssid, priority=priority, autoconnect=autoconnect)
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)

    cfg = ensure_config()
    profiles = _norm_profiles(cfg)
    updated = False
    for item in profiles:
        if item["ssid"] == ssid:
            item["priority"] = priority
            item["autoconnect"] = autoconnect
            updated = True
            break
    if not updated:
        profiles.append({"ssid": ssid, "priority": priority, "autoconnect": autoconnect})
    ok, err = _save_wifi_profiles(cfg, profiles, last_ssid=ssid if not connect_error else "")
    if not ok:
        return _error("config_write_failed", "Profile updated but config write failed", status=500, detail=err)
    payload = {"ssid": ssid, "priority": priority, "autoconnect": autoconnect}
    if connect_error:
        payload["warning"] = f"Profil gespeichert, Verbindung nicht bestätigt: {connect_error}"
    return _ok(payload)


@bp_network.post("/api/wifi/profiles/delete")
def api_wifi_profiles_delete():
    data = request.get_json(force=True, silent=True) or {}
    ssid = (data.get("ssid") or "").strip()
    if not ssid:
        return _error("invalid_payload", "Field 'ssid' is required", status=400)
    try:
        wifi_profile_delete(ssid)
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)
    cfg = ensure_config()
    profiles = [item for item in _norm_profiles(cfg) if item["ssid"] != ssid]
    preferred = (cfg.get("preferred_wifi") or "")
    if preferred == ssid:
        preferred = ""
    ok, err = _save_wifi_profiles(cfg, profiles, preferred=preferred)
    if not ok:
        return _error("config_write_failed", "Profile deleted but config write failed", status=500, detail=err)
    return _ok({"ssid": ssid})


@bp_network.post("/api/wifi/profiles/prefer")
def api_wifi_profiles_prefer():
    data = request.get_json(force=True, silent=True) or {}
    ssid = (data.get("ssid") or "").strip()
    if not ssid:
        return _error("invalid_payload", "Field 'ssid' is required", status=400)

    cfg = ensure_config()
    profiles = _norm_profiles(cfg)
    found = False
    for item in profiles:
        if item["ssid"] == ssid:
            item["priority"] = PREFERRED_WIFI_PRIORITY
            item["autoconnect"] = True
            found = True
        elif int(item.get("priority") or 0) >= PREFERRED_WIFI_PRIORITY:
            item["priority"] = NONPREFERRED_WIFI_PRIORITY
    if not found:
        profiles.append({"ssid": ssid, "priority": PREFERRED_WIFI_PRIORITY, "autoconnect": True})

    try:
        wifi_profile_set(ssid=ssid, priority=PREFERRED_WIFI_PRIORITY, autoconnect=True)
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)

    ok, err = _save_wifi_profiles(cfg, profiles, preferred=ssid)
    if not ok:
        return _error("config_write_failed", "Preferred profile updated but config write failed", status=500, detail=err)
    return _ok({"preferred_ssid": ssid, "profiles": profiles})


@bp_network.post("/api/wifi/profiles/up")
def api_wifi_profiles_up():
    data = request.get_json(force=True, silent=True) or {}
    ssid = (data.get("ssid") or "").strip()
    if not ssid:
        return _error("invalid_payload", "Field 'ssid' is required", status=400)
    try:
        result = wifi_profile_up(ssid)
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)
    cfg = ensure_config()
    ok, err = _save_wifi_profiles(cfg, _norm_profiles(cfg), last_ssid=ssid)
    if not ok:
        return _error("config_write_failed", "Profile activated but state persistence failed", status=500, detail=err)
    return _ok(result)


@bp_network.post("/api/wifi/profiles/apply")
def api_wifi_profiles_apply():
    cfg = ensure_config()
    preferred = (cfg.get("preferred_wifi") or "").strip()
    profiles = _norm_profiles(cfg)
    if preferred:
        for item in profiles:
            if item["ssid"] == preferred:
                item["priority"] = max(int(item.get("priority") or 0), PREFERRED_WIFI_PRIORITY)
                item["autoconnect"] = True
            elif int(item.get("priority") or 0) >= PREFERRED_WIFI_PRIORITY:
                item["priority"] = NONPREFERRED_WIFI_PRIORITY
    logs: list[str] = []
    for item in profiles:
        try:
            wifi_profile_set(item["ssid"], int(item.get("priority") or 0), bool(item.get("autoconnect", True)))
            logs.append(f"[set] {item['ssid']} ok")
        except NetControlError as exc:
            logs.append(f"[set] {item['ssid']} failed: {exc.detail or exc.message}")
    connected_ssid = ""
    for item in sorted(profiles, key=lambda p: int(p.get("priority") or 0), reverse=True):
        if not item.get("autoconnect", True):
            continue
        try:
            wifi_profile_up(item["ssid"])
            connected_ssid = item["ssid"]
            logs.append(f"[up] {item['ssid']} ok")
            break
        except NetControlError as exc:
            logs.append(f"[up] {item['ssid']} failed: {exc.detail or exc.message}")
    ok, err = _save_wifi_profiles(cfg, profiles, preferred=preferred, last_ssid=connected_ssid)
    if not ok:
        return _error("config_write_failed", "Profiles applied but state persistence failed", status=500, detail=err)
    return _ok({"connected_ssid": connected_ssid, "logs": logs, "profiles": profiles})


@bp_network.post("/api/system/tailscale/disable-dns")
def api_system_tailscale_disable_dns():
    try:
        result = disable_tailscale_dns_override()
        return _ok(result)
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)
