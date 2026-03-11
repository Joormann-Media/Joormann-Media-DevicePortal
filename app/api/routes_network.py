from __future__ import annotations

import ipaddress
import threading
import time
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

from app.core.config import ensure_config
from app.core.device import ensure_device
from app.core.display import DISPLAY_MOUNT_ORIENTATIONS, get_display_snapshot, normalize_mount_orientation, update_display_config
from app.core.fingerprint import collect_fingerprint
from app.core.jsonio import read_json, write_json
from app.core.network_events import (
    get_bt_pairing_state,
    get_wps_state,
    log_event,
    read_events,
    set_bt_pairing_state,
    set_wps_state,
)
from app.core.netcontrol import (
    NONPREFERRED_WIFI_PRIORITY,
    PREFERRED_WIFI_PRIORITY,
    apply_hostname_rename,
    hostname_rename_preview,
    NetControlError,
    disable_tailscale_dns_override,
    get_ap_clients,
    get_ap_status,
    get_wifi_status,
    get_network_info,
    portal_update,
    portal_update_status,
    player_service_action,
    restart_portal_service,
    set_ap_enabled,
    get_bluetooth_status,
    get_bluetooth_pairing_feedback,
    get_bluetooth_pairing_session_status,
    get_bluetooth_paired_devices,
    bluetooth_pairing_action,
    set_bluetooth_enabled,
    set_bluetooth_runtime_settings,
    start_bluetooth_pairing_session,
    stop_bluetooth_pairing_session,
    set_lan_enabled,
    system_power_action,
    set_wifi_enabled,
    start_wps,
    wifi_connect,
    wifi_disconnect,
    wifi_profile_delete,
    wifi_profile_set,
    wifi_profile_up,
    wifi_profiles_list,
    wifi_request_dhcp,
    wifi_scan,
)
from app.core.paths import CONFIG_PATH, DATA_DIR
from app.core.storage_file_manager import StorageDeleteService, StorageFileManagerService
from app.core.storage_state import (
    format_storage_device,
    get_storage_state,
    ignore_storage_device,
    mount_storage_device,
    register_storage_device,
    remove_storage_device,
    set_storage_auto_mount,
    set_storage_enabled,
    unmount_storage_device,
    unignore_storage_device,
)
from app.core.timeutil import utc_now
from app.core.state import update_state
from app.core.sentinel_manager import (
    SentinelManagerError,
    get_status as sentinels_status,
    install_sentinel as install_sentinel_module,
    uninstall_sentinel as uninstall_sentinel_module,
)

bp_network = Blueprint("network", __name__)
storage_fm = StorageFileManagerService()
storage_delete_service = StorageDeleteService(storage_fm)
PLAYER_SOURCE_PATH = Path(DATA_DIR) / "player-source.json"


def _ok(data: dict, status: int = 200):
    message = data.get("message") if isinstance(data, dict) else ""
    return jsonify(ok=True, success=True, message=message or "ok", data=data, error_code=""), status


def _error(code: str, message: str, status: int = 400, detail: str = ""):
    payload = {"code": code, "message": message}
    if detail:
        payload["detail"] = detail
    return jsonify(ok=False, success=False, message=message, data={}, error_code=code, error=payload), status


def _update_player_source_display(snapshot: dict) -> tuple[bool, str]:
    payload = read_json(str(PLAYER_SOURCE_PATH), None)
    if not isinstance(payload, dict):
        # Kein Stream aktiv bzw. kein Source-File vorhanden -> kein Fehlerfall.
        return True, ""

    primary = snapshot.get("primary_display") if isinstance(snapshot.get("primary_display"), dict) else {}
    if not isinstance(primary, dict):
        primary = {}

    display = payload.get("display") if isinstance(payload.get("display"), dict) else {}
    display["rotation_degrees"] = int(primary.get("rotation_degrees") or 0)
    display["mount_orientation"] = str(primary.get("mount_orientation") or "unknown")
    display["content_orientation"] = str(primary.get("content_orientation") or "landscape")
    payload["display"] = display
    payload["updated_at"] = utc_now()

    ok, err = write_json(str(PLAYER_SOURCE_PATH), payload, mode=0o600)
    return ok, err


def _wps_phase_from_status(status: dict, wps_state: dict) -> dict:
    now = time.time()
    started_at = float(wps_state.get("started_at_ts") or 0)
    active = bool(wps_state.get("active"))
    elapsed = int(max(0, now - started_at)) if started_at else 0
    wpa_state = (status.get("wpa_state") or "").upper()
    connected = bool(status.get("connected"))
    ip = (status.get("ip") or "").strip()

    if not active:
        phase = "idle"
        message = "WPS inaktiv."
    elif elapsed > 125 and not connected:
        phase = "timeout"
        message = "WPS Zeitüberschreitung."
    elif connected and ip:
        phase = "connected"
        message = "WLAN verbunden und IP vorhanden."
    elif connected and not ip:
        phase = "dhcp_request"
        message = "WLAN verbunden, DHCP wird angefordert."
    elif wpa_state in ("SCANNING",):
        phase = "router_search"
        message = "Router wird gesucht."
    elif wpa_state in ("ASSOCIATING", "ASSOCIATED", "4WAY_HANDSHAKE", "AUTHENTICATING"):
        phase = "auth"
        message = "Authentifizierung läuft."
    elif wpa_state in ("DISCONNECTED", "INACTIVE", "INTERFACE_DISABLED"):
        phase = "started"
        message = "WPS gestartet. Warte auf Router."
    else:
        phase = "in_progress"
        message = f"WPS-Status: {wpa_state or 'unbekannt'}"
    return {
        "phase": phase,
        "phase_message": message,
        "wpa_state": wpa_state,
        "elapsed_sec": elapsed,
        "active": active,
    }


def _disable_ap_after_wifi_uplink(ifname: str = "wlan0", reason: str = "") -> dict:
    result = {"attempted": False, "disabled": False, "was_enabled": False, "ifname": ifname, "reason": reason}
    try:
        ap_status = get_ap_status(ifname=ifname)
    except NetControlError as exc:
        result["error"] = exc.detail or exc.message
        log_event("ap", "Could not read AP status after Wi-Fi uplink", level="warning", data=result)
        return result

    active = bool(ap_status.get("active"))
    result["was_enabled"] = active
    if not active:
        return result

    profile = (ap_status.get("profile") or "jm-hotspot").strip() or "jm-hotspot"
    result["attempted"] = True
    try:
        set_ap_enabled(False, ifname=ifname, profile=profile)
        result["disabled"] = True
        result["player_sync"] = _sync_player_with_ap_mode(False, source="wifi_uplink_disable_ap")
        log_event("ap", "AP disabled after Wi-Fi uplink", data={**result, "profile": profile})
    except NetControlError as exc:
        result["error"] = exc.detail or exc.message
        log_event("ap", "Failed to disable AP after Wi-Fi uplink", level="warning", data={**result, "profile": profile})
    return result


def _prepare_client_uplink_switch(ifname: str = "wlan0", reason: str = "") -> dict:
    """
    Best-effort pre-switch: if AP is currently active on wlan0, disable it before
    client connect/profile-up to avoid AP/client contention on single-radio devices.
    """
    result = {"attempted": False, "disabled": False, "was_enabled": False, "ifname": ifname, "reason": reason}
    try:
        ap_status = get_ap_status(ifname=ifname)
    except NetControlError as exc:
        result["error"] = exc.detail or exc.message
        return result

    active = bool(ap_status.get("active"))
    result["was_enabled"] = active
    if not active:
        return result

    result["attempted"] = True
    profile = (ap_status.get("profile") or "jm-hotspot").strip() or "jm-hotspot"
    try:
        set_ap_enabled(False, ifname=ifname, profile=profile)
        result["disabled"] = True
    except NetControlError as exc:
        result["error"] = exc.detail or exc.message
    return result


def _sync_player_with_ap_mode(ap_enabled: bool, source: str = "") -> dict:
    cfg = ensure_config()
    service_name = str(cfg.get("player_service_name") or "joormann-media-deviceplayer.service").strip() or "joormann-media-deviceplayer.service"
    action = "stop" if ap_enabled else "start"
    result = {"ok": False, "action": action, "service_name": service_name, "source": source, "error": ""}
    try:
        payload = player_service_action(action, service_name=service_name)
        result["ok"] = bool(payload.get("ok"))
        result["message"] = str(payload.get("message") or "")
        return result
    except NetControlError as exc:
        result["error"] = exc.detail or exc.message
        log_event(
            "ap",
            "Failed to sync player service with AP mode",
            level="warning",
            data={"source": source, "action": action, "service_name": service_name, "error": result["error"]},
        )
        return result


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


def _is_ap_client_request(ifname: str = "wlan0") -> bool:
    remote_addr = (request.remote_addr or "").strip()
    if not remote_addr:
        return False
    try:
        remote_ip = ipaddress.ip_address(remote_addr)
    except ValueError:
        return False
    if remote_ip.is_loopback:
        return False

    candidate_networks: list[ipaddress._BaseNetwork] = []
    for cidr in ("192.168.4.0/24", "10.42.0.0/24"):
        try:
            candidate_networks.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            pass

    try:
        ap_status = get_ap_status(ifname=ifname)
    except NetControlError:
        ap_status = {}

    ap_ip = str((ap_status or {}).get("ip") or "").strip()
    if ap_ip:
        try:
            # AP scripts expose IP only; use /24 as sane default for hotspot subnets.
            candidate_networks.append(ipaddress.ip_network(f"{ap_ip}/24", strict=False))
        except ValueError:
            pass

    return any(remote_ip in net for net in candidate_networks)


def _run_wps_start_async(ifname: str, target_bssid: str, target_ssid: str) -> None:
    # Give the HTTP response a head start so AP clients receive the success payload
    # before wlan0 switches away from hotspot mode.
    time.sleep(2.0)
    try:
        result = start_wps(ifname=ifname, target_bssid=target_bssid, target_ssid=target_ssid)
        log_event("wps", result.get("message", "WPS started (async)"), data={"iface": ifname, "code": result.get("code", "ok"), "async": True})
    except NetControlError as exc:
        log_event("wps", exc.message, level="error", data={"iface": ifname, "code": exc.code, "detail": exc.detail, "async": True})
        set_wps_state({"active": False, "ifname": ifname, "finished_at_ts": time.time(), "result": exc.code})


def _save_wifi_profiles(cfg: dict, profiles: list[dict], preferred: str = "", last_ssid: str = "") -> tuple[bool, str]:
    cfg["wifi_profiles"] = profiles
    if preferred:
        cfg["preferred_wifi"] = preferred
    if last_ssid:
        cfg["last_wifi_ssid"] = last_ssid
    cfg["updated_at"] = utc_now()
    return write_json(CONFIG_PATH, cfg, mode=0o600)


def _current_ap_ssid(ifname: str = "wlan0", profile: str = "jm-hotspot") -> str:
    try:
        ap = get_ap_status(ifname=ifname, profile=profile)
        return str(ap.get("ssid") or "").strip()
    except NetControlError:
        return ""


def _is_ap_ssid(ssid: str, ifname: str = "wlan0", profile: str = "jm-hotspot") -> bool:
    target = str(ssid or "").strip()
    if not target:
        return False
    ap_ssid = _current_ap_ssid(ifname=ifname, profile=profile)
    if not ap_ssid:
        return False
    return target.casefold() == ap_ssid.casefold()


def _purge_ap_ssid_profile_if_needed(cfg: dict, ifname: str = "wlan0", profile: str = "jm-hotspot") -> None:
    ap_ssid = _current_ap_ssid(ifname=ifname, profile=profile)
    if not ap_ssid:
        return
    profiles = _norm_profiles(cfg)
    kept = [item for item in profiles if str(item.get("ssid") or "").casefold() != ap_ssid.casefold()]
    if len(kept) != len(profiles):
        preferred = str(cfg.get("preferred_wifi") or "")
        preferred_out = "" if preferred.casefold() == ap_ssid.casefold() else preferred
        _save_wifi_profiles(cfg, kept, preferred=preferred_out)
        try:
            wifi_profile_delete(ap_ssid)
        except NetControlError:
            pass
        log_event("wifi", "AP SSID removed from known Wi-Fi profiles", level="warning", data={"ssid": ap_ssid})


def _merged_profiles(profiles_cfg: list[dict], nm_profiles: list[dict], preferred: str, last_ssid: str) -> list[dict]:
    merged: dict[str, dict] = {}

    for item in nm_profiles:
        ssid = (item.get("name") or "").strip()
        if not ssid:
            continue
        merged[ssid] = {
            "ssid": ssid,
            "priority": int(item.get("priority") or 0),
            "autoconnect": bool(item.get("autoconnect", True)),
            "exists": True,
            "source": "nm",
            "preferred": bool(preferred and ssid == preferred),
            "last": bool(last_ssid and ssid == last_ssid),
            "nm": item,
        }

    for item in profiles_cfg:
        ssid = item["ssid"]
        current = merged.get(ssid)
        nm_item = current.get("nm") if current else None
        merged[ssid] = {
            "ssid": ssid,
            "priority": int(item.get("priority") or (nm_item or {}).get("priority") or 0),
            "autoconnect": bool(item.get("autoconnect", (nm_item or {}).get("autoconnect", True))),
            "exists": bool(current),
            "source": "config+nm" if current else "config",
            "preferred": bool(preferred and ssid == preferred),
            "last": bool(last_ssid and ssid == last_ssid),
            "nm": nm_item,
        }

    result = list(merged.values())
    result.sort(key=lambda p: (int(p.get("priority") or 0), p.get("ssid") or ""), reverse=True)
    return result


def _norm_text(value: object) -> str:
    return str(value or "").strip()


def _norm_mac(value: object) -> str:
    return _norm_text(value).upper()


def _is_unknown_connection_detail(detail: str) -> bool:
    text = (detail or "").strip().lower()
    if not text:
        return False
    markers = (
        "unknown connection",
        "unknown connections",
        "unbekannte verbindung",
        "nicht gefunden",
    )
    return any(marker in text for marker in markers)


def _normalize_network_security(raw: object) -> dict:
    data = raw if isinstance(raw, dict) else {}
    trusted_wifi_raw = data.get("trusted_wifi")
    trusted_lan_raw = data.get("trusted_lan")
    trusted_bt_raw = data.get("trusted_bluetooth")
    trusted_wifi = trusted_wifi_raw if isinstance(trusted_wifi_raw, list) else []
    trusted_lan = trusted_lan_raw if isinstance(trusted_lan_raw, list) else []
    trusted_bt = trusted_bt_raw if isinstance(trusted_bt_raw, list) else []

    wifi_rows: list[dict] = []
    seen_wifi: set[str] = set()
    for item in trusted_wifi:
        if not isinstance(item, dict):
            continue
        ssid = _norm_text(item.get("ssid"))
        bssid = _norm_mac(item.get("bssid"))
        key = bssid or ssid
        if key == "" or key in seen_wifi:
            continue
        seen_wifi.add(key)
        wifi_rows.append(
            {
                "key": key,
                "ssid": ssid,
                "bssid": bssid,
                "label": _norm_text(item.get("label")) or (f"{ssid} ({bssid})" if ssid and bssid else ssid or bssid),
                "last_seen": _norm_text(item.get("last_seen")),
            }
        )

    lan_rows: list[dict] = []
    seen_lan: set[str] = set()
    for item in trusted_lan:
        if not isinstance(item, dict):
            continue
        gateway_ip = _norm_text(item.get("gateway_ip"))
        gateway_mac = _norm_mac(item.get("gateway_mac"))
        connection = _norm_text(item.get("connection"))
        ifname = _norm_text(item.get("ifname")) or "eth0"
        key = "|".join([gateway_ip, gateway_mac, connection, ifname]).strip("|")
        if key == "" or key in seen_lan:
            continue
        seen_lan.add(key)
        lan_rows.append(
            {
                "key": key,
                "gateway_ip": gateway_ip,
                "gateway_mac": gateway_mac,
                "connection": connection,
                "ifname": ifname,
                "label": _norm_text(item.get("label")) or (connection or gateway_mac or gateway_ip or ifname),
                "last_seen": _norm_text(item.get("last_seen")),
            }
        )

    bt_rows: list[dict] = []
    seen_bt: set[str] = set()
    for item in trusted_bt:
        if not isinstance(item, dict):
            continue
        mac = _norm_mac(item.get("mac"))
        if mac == "" or mac in seen_bt:
            continue
        seen_bt.add(mac)
        bt_rows.append(
            {
                "key": mac,
                "mac": mac,
                "name": _norm_text(item.get("name")),
                "label": _norm_text(item.get("label")) or (_norm_text(item.get("name")) or mac),
                "last_seen": _norm_text(item.get("last_seen")),
            }
        )

    return {
        "enabled": bool(data.get("enabled", False)),
        "trusted_wifi": wifi_rows,
        "trusted_lan": lan_rows,
        "trusted_bluetooth": bt_rows,
        "updated_at": _norm_text(data.get("updated_at")),
    }


def _get_network_security_profile(cfg: dict) -> dict:
    profile = _normalize_network_security(cfg.get("network_security"))
    cfg["network_security"] = profile
    return profile


def _security_assessment(profile: dict, network_info: dict, bt_paired: list[dict]) -> dict:
    interfaces = (network_info or {}).get("interfaces") or {}
    routes = (network_info or {}).get("routes") or {}
    wifi = interfaces.get("wifi") or {}
    lan = interfaces.get("lan") or {}

    active_wifi = bool(wifi.get("connected"))
    active_lan = bool(lan.get("carrier") or _norm_text(lan.get("ip")))
    active_bt = len(bt_paired) > 0

    trusted_wifi = profile.get("trusted_wifi") or []
    trusted_lan = profile.get("trusted_lan") or []
    trusted_bt = profile.get("trusted_bluetooth") or []

    wifi_ssid = _norm_text(wifi.get("ssid"))
    wifi_bssid = _norm_mac(wifi.get("bssid"))
    lan_gateway_ip = _norm_text(routes.get("gateway"))
    lan_gateway_mac = _norm_mac(routes.get("gateway_mac"))
    lan_connection = _norm_text(lan.get("connection"))
    lan_ifname = _norm_text(lan.get("ifname"))

    wifi_match = None
    if active_wifi:
        for item in trusted_wifi:
            if (item.get("bssid") and item.get("bssid") == wifi_bssid) or (item.get("ssid") and item.get("ssid") == wifi_ssid):
                wifi_match = item
                break

    lan_match = None
    if active_lan:
        for item in trusted_lan:
            by_mac = bool(item.get("gateway_mac")) and item.get("gateway_mac") == lan_gateway_mac
            by_ip = bool(item.get("gateway_ip")) and item.get("gateway_ip") == lan_gateway_ip
            by_conn = bool(item.get("connection")) and item.get("connection") == lan_connection
            by_if = bool(item.get("ifname")) and item.get("ifname") == lan_ifname
            if by_mac or by_ip or by_conn or by_if:
                lan_match = item
                break

    bt_match = None
    if active_bt:
        trusted_macs = {_norm_mac(item.get("mac")) for item in trusted_bt}
        for dev in bt_paired:
            dev_mac = _norm_mac(dev.get("mac"))
            if dev_mac in trusted_macs:
                bt_match = dev
                break

    perimeter_enabled = bool(profile.get("enabled"))
    in_perimeter = True
    if perimeter_enabled:
        in_perimeter = bool(wifi_match or lan_match or bt_match)

    return {
        "enabled": perimeter_enabled,
        "in_perimeter": in_perimeter,
        "active": {
            "wifi": active_wifi,
            "lan": active_lan,
            "bluetooth": active_bt,
        },
        "matches": {
            "wifi": wifi_match or {},
            "lan": lan_match or {},
            "bluetooth": bt_match or {},
        },
        "current": {
            "wifi": {"ssid": wifi_ssid, "bssid": wifi_bssid},
            "lan": {
                "ifname": lan_ifname,
                "connection": lan_connection,
                "gateway_ip": lan_gateway_ip,
                "gateway_mac": lan_gateway_mac,
            },
            "bluetooth": bt_paired,
        },
    }


def _network_security_catalog(cfg: dict, network_info: dict, bt_paired: list[dict]) -> dict:
    wifi_known: list[dict] = []
    seen_wifi: set[str] = set()
    profiles_cfg = _norm_profiles(cfg)
    for item in profiles_cfg:
        ssid = _norm_text(item.get("ssid"))
        if not ssid or ssid in seen_wifi:
            continue
        seen_wifi.add(ssid)
        wifi_known.append(
            {
                "key": ssid,
                "ssid": ssid,
                "source": "config",
            }
        )
    try:
        nm_profiles = wifi_profiles_list().get("profiles", [])
    except NetControlError:
        nm_profiles = []
    for item in nm_profiles:
        ssid = _norm_text(item.get("name"))
        if not ssid or ssid in seen_wifi:
            continue
        seen_wifi.add(ssid)
        wifi_known.append(
            {
                "key": ssid,
                "ssid": ssid,
                "source": "nm",
            }
        )

    lan = ((network_info or {}).get("interfaces") or {}).get("lan") or {}
    routes = (network_info or {}).get("routes") or {}
    lan_known = []
    if bool(lan.get("carrier") or _norm_text(lan.get("ip"))):
        lan_known.append(
            {
                "key": "|".join(
                    [
                        _norm_text(routes.get("gateway")),
                        _norm_mac(routes.get("gateway_mac")),
                        _norm_text(lan.get("connection")),
                        _norm_text(lan.get("ifname")),
                    ]
                ).strip("|"),
                "ifname": _norm_text(lan.get("ifname")),
                "connection": _norm_text(lan.get("connection")),
                "gateway_ip": _norm_text(routes.get("gateway")),
                "gateway_mac": _norm_mac(routes.get("gateway_mac")),
            }
        )

    bt_known = []
    for dev in bt_paired:
        mac = _norm_mac(dev.get("mac"))
        if not mac:
            continue
        bt_known.append(
            {
                "key": mac,
                "mac": mac,
                "name": _norm_text(dev.get("name")),
            }
        )

    return {
        "known_wifi": wifi_known,
        "known_lan": lan_known,
        "known_bluetooth": bt_known,
    }


def _has_uplink(network_info: dict) -> bool:
    interfaces = (network_info or {}).get("interfaces") or {}
    lan = interfaces.get("lan") or {}
    wifi = interfaces.get("wifi") or {}

    lan_up = bool(lan.get("carrier")) or bool(_norm_text(lan.get("ip")))
    wifi_up = bool(wifi.get("connected"))
    return bool(lan_up or wifi_up)


@bp_network.get("/api/network/info")
def api_network_info():
    try:
        info = get_network_info()
        setup_mode_active = not _has_uplink(info)
        setup_mode = {
            "active": setup_mode_active,
            "reason": "missing_lan_and_wifi_uplink" if setup_mode_active else "uplink_available",
            "message": (
                "Kein LAN/WLAN-Uplink erkannt. AP/Hotspot wird als Setup-Fallback aktiv gehalten."
                if setup_mode_active
                else "Uplink verfügbar."
            ),
            "ap_auto_enable_attempted": False,
            "ap_auto_enabled": False,
            "ap_error": "",
            "ap": {},
        }

        if setup_mode_active:
            setup_mode["ap_auto_enable_attempted"] = True
            try:
                ap_status = get_ap_status()
                if not bool(ap_status.get("active")):
                    try:
                        set_wifi_enabled(True)
                    except NetControlError:
                        # Continue with AP activation attempt; script may recover itself.
                        pass
                    set_ap_enabled(True)
                    setup_mode["ap_auto_enabled"] = True
                    log_event(
                        "ap",
                        "AP auto-enabled because no LAN/WLAN uplink is available",
                        level="warning",
                        data={"reason": "missing_lan_and_wifi_uplink"},
                    )
                    _sync_player_with_ap_mode(True, source="network_info_auto_ap")
                    ap_status = get_ap_status()
                setup_mode["ap"] = ap_status
            except NetControlError as exc:
                setup_mode["ap_error"] = f"{exc.code}: {exc.message}"

        info["connectivity_setup_mode"] = setup_mode
        cfg = ensure_config()
        profile = _get_network_security_profile(cfg)
        try:
            bt_paired = get_bluetooth_paired_devices()
        except NetControlError:
            bt_paired = []
        assessment = _security_assessment(profile, info, bt_paired)
        catalog = _network_security_catalog(cfg, info, bt_paired)
        info["security"] = {
            "profile": profile,
            "assessment": assessment,
            "catalog": catalog,
        }
        return _ok(info)
    except NetControlError as exc:
        http_status = 500 if exc.code in ("script_missing", "execution_failed") else 400
        return _error(exc.code, exc.message, status=http_status, detail=exc.detail)


@bp_network.get("/api/network/security/status")
def api_network_security_status():
    cfg = ensure_config()
    profile = _get_network_security_profile(cfg)
    try:
        info = get_network_info()
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)
    try:
        bt_paired = get_bluetooth_paired_devices()
    except NetControlError:
        bt_paired = []
    assessment = _security_assessment(profile, info, bt_paired)
    catalog = _network_security_catalog(cfg, info, bt_paired)
    return _ok(
        {
            "profile": profile,
            "assessment": assessment,
            "catalog": catalog,
        }
    )


@bp_network.post("/api/network/security/settings")
def api_network_security_settings():
    data = request.get_json(force=True, silent=True) or {}
    if "enabled" not in data or not isinstance(data.get("enabled"), bool):
        return _error("invalid_payload", "Field 'enabled' (bool) is required", status=400)
    cfg = ensure_config()
    profile = _get_network_security_profile(cfg)
    profile["enabled"] = bool(data.get("enabled"))
    profile["updated_at"] = utc_now()
    cfg["network_security"] = profile
    cfg["updated_at"] = utc_now()
    ok, err = write_json(CONFIG_PATH, cfg, mode=0o600)
    if not ok:
        return _error("config_write_failed", "Could not persist network security settings", status=500, detail=err)
    return _ok({"profile": profile})


@bp_network.post("/api/network/security/trust/current")
def api_network_security_trust_current():
    data = request.get_json(force=True, silent=True) or {}
    trust_wifi = bool(data.get("wifi", True))
    trust_lan = bool(data.get("lan", True))
    trust_bt = bool(data.get("bluetooth", True))
    cfg = ensure_config()
    profile = _get_network_security_profile(cfg)
    now = utc_now()

    try:
        info = get_network_info()
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)

    interfaces = (info.get("interfaces") or {})
    routes = (info.get("routes") or {})
    wifi = interfaces.get("wifi") or {}
    lan = interfaces.get("lan") or {}

    added = {"wifi": 0, "lan": 0, "bluetooth": 0}

    if trust_wifi:
        trusted_wifi = profile.get("trusted_wifi") or []
        existing_keys = {_norm_text(item.get("key")) for item in trusted_wifi if isinstance(item, dict)}
        wifi_candidates: list[tuple[str, str, str]] = []
        if bool(wifi.get("connected")):
            ssid = _norm_text(wifi.get("ssid"))
            bssid = _norm_mac(wifi.get("bssid"))
            key = bssid or ssid
            if key:
                wifi_candidates.append((key, ssid, bssid))
        if not wifi_candidates:
            for item in _norm_profiles(cfg):
                ssid = _norm_text(item.get("ssid"))
                if ssid:
                    wifi_candidates.append((ssid, ssid, ""))
            try:
                for item in wifi_profiles_list().get("profiles", []):
                    ssid = _norm_text(item.get("name"))
                    if ssid:
                        wifi_candidates.append((ssid, ssid, ""))
            except NetControlError:
                pass
        seen_candidate: set[str] = set()
        for key, ssid, bssid in wifi_candidates:
            if not key or key in seen_candidate or key in existing_keys:
                continue
            seen_candidate.add(key)
            trusted_wifi.append(
                {
                    "key": key,
                    "ssid": ssid,
                    "bssid": bssid,
                    "label": f"{ssid} ({bssid})" if ssid and bssid else (ssid or bssid or key),
                    "last_seen": now,
                }
            )
            existing_keys.add(key)
            added["wifi"] += 1
        profile["trusted_wifi"] = trusted_wifi

    if trust_lan and bool(lan.get("carrier") or _norm_text(lan.get("ip"))):
        gateway_ip = _norm_text(routes.get("gateway"))
        gateway_mac = _norm_mac(routes.get("gateway_mac"))
        connection = _norm_text(lan.get("connection"))
        ifname = _norm_text(lan.get("ifname")) or "eth0"
        key = "|".join([gateway_ip, gateway_mac, connection, ifname]).strip("|")
        if key:
            trusted_lan = profile.get("trusted_lan") or []
            if not any((_norm_text(item.get("key")) == key) for item in trusted_lan if isinstance(item, dict)):
                trusted_lan.append(
                    {
                        "key": key,
                        "gateway_ip": gateway_ip,
                        "gateway_mac": gateway_mac,
                        "connection": connection,
                        "ifname": ifname,
                        "label": connection or gateway_mac or gateway_ip or ifname,
                        "last_seen": now,
                    }
                )
                profile["trusted_lan"] = trusted_lan
                added["lan"] += 1

    if trust_bt:
        try:
            bt_paired = get_bluetooth_paired_devices()
        except NetControlError:
            bt_paired = []
        trusted_bt = profile.get("trusted_bluetooth") or []
        known = {_norm_mac(item.get("mac")) for item in trusted_bt if isinstance(item, dict)}
        for dev in bt_paired:
            mac = _norm_mac(dev.get("mac"))
            if not mac or mac in known:
                continue
            trusted_bt.append(
                {
                    "key": mac,
                    "mac": mac,
                    "name": _norm_text(dev.get("name")),
                    "label": _norm_text(dev.get("name")) or mac,
                    "last_seen": now,
                }
            )
            known.add(mac)
            added["bluetooth"] += 1
        profile["trusted_bluetooth"] = trusted_bt

    profile = _normalize_network_security(profile)
    profile["updated_at"] = now
    cfg["network_security"] = profile
    cfg["updated_at"] = now
    ok, err = write_json(CONFIG_PATH, cfg, mode=0o600)
    if not ok:
        return _error("config_write_failed", "Could not persist trusted network markers", status=500, detail=err)

    try:
        bt_now = get_bluetooth_paired_devices()
    except NetControlError:
        bt_now = []
    assessment = _security_assessment(profile, info, bt_now)
    return _ok({"profile": profile, "assessment": assessment, "added": added})


@bp_network.post("/api/network/security/trust/remove")
def api_network_security_trust_remove():
    data = request.get_json(force=True, silent=True) or {}
    kind = _norm_text(data.get("kind")).lower()
    key = _norm_text(data.get("key"))
    if kind not in ("wifi", "lan", "bluetooth"):
        return _error("invalid_payload", "Field 'kind' must be one of wifi|lan|bluetooth", status=400)
    if not key:
        return _error("invalid_payload", "Field 'key' is required", status=400)
    cfg = ensure_config()
    profile = _get_network_security_profile(cfg)
    list_key = "trusted_wifi" if kind == "wifi" else ("trusted_lan" if kind == "lan" else "trusted_bluetooth")
    before = len(profile.get(list_key) or [])
    profile[list_key] = [item for item in (profile.get(list_key) or []) if _norm_text((item or {}).get("key")) != key]
    removed = max(0, before - len(profile.get(list_key) or []))
    profile["updated_at"] = utc_now()
    cfg["network_security"] = profile
    cfg["updated_at"] = utc_now()
    ok, err = write_json(CONFIG_PATH, cfg, mode=0o600)
    if not ok:
        return _error("config_write_failed", "Could not persist trust removal", status=500, detail=err)
    return _ok({"profile": profile, "removed": removed, "kind": kind, "key": key})


@bp_network.get("/api/display/info")
def api_display_info():
    cfg = ensure_config()
    return _ok(get_display_snapshot(cfg))


@bp_network.get("/api/network/display/info")
def api_network_display_info():
    return api_display_info()


@bp_network.post("/api/display/config")
def api_display_config():
    data = request.get_json(force=True, silent=True) or {}
    connector = str(data.get("connector") or "").strip()
    if not connector:
        return _error("invalid_payload", "Field 'connector' is required", status=400)

    mount_orientation = None
    if "mount_orientation" in data:
        mount_orientation_raw = str(data.get("mount_orientation") or "").strip()
        mount_orientation = normalize_mount_orientation(mount_orientation_raw)
        if mount_orientation_raw and mount_orientation == "unknown" and mount_orientation_raw.lower() not in ("unknown", "horizontal", "vertical"):
            return _error(
                "invalid_mount_orientation",
                "Unsupported mount_orientation",
                status=400,
                detail=f"Allowed: {', '.join(DISPLAY_MOUNT_ORIENTATIONS)}",
            )

    active = None
    if "active" in data:
        if not isinstance(data.get("active"), bool):
            return _error("invalid_payload", "Field 'active' must be boolean", status=400)
        active = bool(data.get("active"))

    friendly_name = None
    if "friendly_name" in data:
        friendly_name = str(data.get("friendly_name") or "").strip()

    note = None
    if "note" in data:
        note = str(data.get("note") or "").strip()

    cfg = ensure_config()
    try:
        updated_item = update_display_config(
            cfg,
            connector=connector,
            mount_orientation=mount_orientation,
            active=active,
            friendly_name=friendly_name,
            note=note,
        )
    except ValueError as exc:
        return _error("invalid_payload", str(exc), status=400)

    ok_write, write_err = write_json(CONFIG_PATH, cfg, mode=0o600)
    if not ok_write:
        return _error("config_write_failed", "Could not persist display config", status=500, detail=write_err)

    snapshot = get_display_snapshot(cfg)
    ps_ok, ps_err = _update_player_source_display(snapshot)
    if not ps_ok:
        return _error("player_source_write_failed", "Display-Update konnte nicht in player-source.json geschrieben werden.", status=500, detail=ps_err)

    return _ok(
        {
            "saved": True,
            "connector": connector,
            "display_config": updated_item,
            "display": snapshot,
            "player_source_updated": True,
            "allowed_mount_orientations": list(DISPLAY_MOUNT_ORIENTATIONS),
        }
    )


@bp_network.post("/api/network/display/config")
def api_network_display_config():
    return api_display_config()


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
        try:
            status = get_bluetooth_status()
            for key in ("discoverable", "pairable", "discoverable_timeout", "pairable_timeout"):
                if key in status:
                    result[key] = status.get(key)
        except NetControlError:
            pass
        return _ok(result)
    except NetControlError as exc:
        status = 500 if exc.code == "script_missing" else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.get("/api/network/bluetooth/status")
def api_network_bluetooth_status():
    try:
        result = get_bluetooth_status()
        return _ok(result)
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.post("/api/network/bluetooth/config")
def api_network_bluetooth_config():
    data = request.get_json(force=True, silent=True) or {}

    discoverable = data.get("discoverable")
    if discoverable is not None and not isinstance(discoverable, bool):
        return _error("invalid_payload", "Field 'discoverable' must be bool when provided", status=400)

    pairable = data.get("pairable")
    if pairable is not None and not isinstance(pairable, bool):
        return _error("invalid_payload", "Field 'pairable' must be bool when provided", status=400)

    discoverable_timeout = data.get("discoverable_timeout")
    if discoverable_timeout is not None and not isinstance(discoverable_timeout, int):
        return _error("invalid_payload", "Field 'discoverable_timeout' must be int when provided", status=400)

    pairable_timeout = data.get("pairable_timeout")
    if pairable_timeout is not None and not isinstance(pairable_timeout, int):
        return _error("invalid_payload", "Field 'pairable_timeout' must be int when provided", status=400)

    if discoverable is None and pairable is None and discoverable_timeout is None and pairable_timeout is None:
        return _error(
            "invalid_payload",
            "At least one field is required: discoverable, pairable, discoverable_timeout, pairable_timeout",
            status=400,
        )

    try:
        result = set_bluetooth_runtime_settings(
            discoverable=discoverable,
            discoverable_timeout=discoverable_timeout,
            pairable=pairable,
            pairable_timeout=pairable_timeout,
        )
        return _ok(result)
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.post("/api/network/bluetooth/pairing/start")
def api_network_bluetooth_pairing_start():
    data = request.get_json(force=True, silent=True) or {}
    timeout_seconds_raw = data.get("timeout_seconds", 180)
    if not isinstance(timeout_seconds_raw, int):
        return _error("invalid_payload", "Field 'timeout_seconds' must be int", status=400)
    timeout_seconds = max(30, min(900, int(timeout_seconds_raw)))

    try:
        bt_info = get_network_info()
        bt_enabled = bool((((bt_info or {}).get("interfaces") or {}).get("bluetooth") or {}).get("enabled"))
        if not bt_enabled:
            set_bluetooth_enabled(True)
        start_info = start_bluetooth_pairing_session(timeout_seconds=timeout_seconds)
        status = get_bluetooth_status()

        now_ts = int(time.time())
        session_id = uuid.uuid4().hex[:12]
        pairing_state = {
            "active": True,
            "session_id": session_id,
            "started_at_ts": now_ts,
            "expires_at_ts": now_ts + timeout_seconds,
            "timeout_seconds": timeout_seconds,
        }
        set_bt_pairing_state(pairing_state)
        log_event(
            "bluetooth",
            "Bluetooth pairing mode started",
            data={"session_id": session_id, "timeout_seconds": timeout_seconds, "pid": start_info.get("pid")},
        )
        return _ok(
            {
                "started": True,
                "session_id": session_id,
                "timeout_seconds": timeout_seconds,
                "session": start_info,
                "bluetooth": status,
            }
        )
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.post("/api/network/bluetooth/pairing/stop")
def api_network_bluetooth_pairing_stop():
    try:
        stop_info = stop_bluetooth_pairing_session()
        status = get_bluetooth_status()
        state = get_bt_pairing_state()
        state.update(
            {
                "active": False,
                "stopped_at_ts": int(time.time()),
            }
        )
        set_bt_pairing_state(state)
        log_event("bluetooth", "Bluetooth pairing mode stopped")
        return _ok({"stopped": True, "session": stop_info, "bluetooth": status})
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.post("/api/network/bluetooth/pairing/confirm")
def api_network_bluetooth_pairing_confirm():
    data = request.get_json(force=True, silent=True) or {}
    target_mac = str(data.get("target_mac") or "").strip()
    if target_mac == "":
        try:
            feedback = get_bluetooth_pairing_feedback(window_seconds=300)
            target_mac = str(feedback.get("pending_mac") or feedback.get("device_mac") or "").strip()
        except NetControlError:
            target_mac = ""
    if target_mac == "":
        return _error("invalid_payload", "No target_mac available for confirmation", status=400)

    try:
        action_result = bluetooth_pairing_action("confirm", target_mac)
        log_event("bluetooth", "Bluetooth pairing confirmed", data={"target_mac": target_mac, "result": action_result})
        return _ok({"confirmed": True, "target_mac": target_mac, "result": action_result})
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.post("/api/network/bluetooth/pairing/reject")
def api_network_bluetooth_pairing_reject():
    data = request.get_json(force=True, silent=True) or {}
    target_mac = str(data.get("target_mac") or "").strip()
    if target_mac == "":
        try:
            feedback = get_bluetooth_pairing_feedback(window_seconds=300)
            target_mac = str(feedback.get("pending_mac") or feedback.get("device_mac") or "").strip()
        except NetControlError:
            target_mac = ""
    if target_mac == "":
        return _error("invalid_payload", "No target_mac available for reject", status=400)

    try:
        action_result = bluetooth_pairing_action("reject", target_mac)
        log_event("bluetooth", "Bluetooth pairing rejected", data={"target_mac": target_mac, "result": action_result})
        return _ok({"rejected": True, "target_mac": target_mac, "result": action_result})
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.get("/api/network/bluetooth/pairing/status")
def api_network_bluetooth_pairing_status():
    now_ts = int(time.time())
    state = get_bt_pairing_state()
    try:
        session_status = get_bluetooth_pairing_session_status()
    except NetControlError:
        session_status = {"active": False, "pid": None}

    active = bool(state.get("active")) and bool(session_status.get("active"))
    expires_at_ts = int(state.get("expires_at_ts") or 0)
    if active and expires_at_ts > 0 and now_ts >= expires_at_ts:
        try:
            stop_bluetooth_pairing_session()
        except NetControlError:
            pass
        state["active"] = False
        state["expired"] = True
        state["stopped_at_ts"] = now_ts
        set_bt_pairing_state(state)
        active = False

    try:
        bt_status = get_bluetooth_status()
    except NetControlError:
        bt_status = {}

    try:
        feedback = get_bluetooth_pairing_feedback(window_seconds=300)
    except NetControlError:
        feedback = {
            "passkey": "",
            "device_mac": "",
            "device_name": "",
            "passkey_line": "",
            "recent_line": "",
        }

    remaining_seconds = 0
    if active and expires_at_ts > now_ts:
        remaining_seconds = expires_at_ts - now_ts

    return _ok(
        {
            "active": active,
            "session_id": str(state.get("session_id") or ""),
            "timeout_seconds": int(state.get("timeout_seconds") or 0),
            "started_at_ts": int(state.get("started_at_ts") or 0),
            "expires_at_ts": expires_at_ts,
            "remaining_seconds": remaining_seconds,
            "session": session_status,
            "bluetooth": bt_status,
            "feedback": feedback,
        }
    )


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


@bp_network.get("/api/network/ap/status")
def api_network_ap_status():
    ifname = (request.args.get("ifname") or "wlan0").strip()
    profile = (request.args.get("profile") or "jm-hotspot").strip()
    try:
        return _ok(get_ap_status(ifname=ifname, profile=profile))
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.post("/api/network/ap/toggle")
def api_network_ap_toggle():
    data = request.get_json(force=True, silent=True) or {}
    if "enabled" not in data or not isinstance(data.get("enabled"), bool):
        return _error("invalid_payload", "Field 'enabled' (bool) is required", status=400)
    ifname = (data.get("ifname") or "wlan0").strip()
    profile = (data.get("profile") or "jm-hotspot").strip()
    try:
        result = set_ap_enabled(bool(data["enabled"]), ifname=ifname, profile=profile)
        player_sync = _sync_player_with_ap_mode(bool(result.get("enabled")), source="api_network_ap_toggle")
        log_event("ap", "AP hotspot toggled", data={"enabled": result.get("enabled", False), "ifname": ifname, "profile": profile})
        return _ok({**result, "player_sync": player_sync})
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.get("/api/network/ap/clients")
def api_network_ap_clients():
    ifname = (request.args.get("ifname") or "wlan0").strip()
    try:
        return _ok(get_ap_clients(ifname=ifname))
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.get("/api/network/storage/status")
def api_network_storage_status():
    try:
        return _ok(get_storage_state())
    except NetControlError as exc:
        status = 500 if exc.code in ("storage_probe_failed", "storage_config_write_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.post("/api/network/storage/register")
def api_network_storage_register():
    data = request.get_json(force=True, silent=True) or {}
    device_id = str(data.get("device_id") or "").strip()
    name = str(data.get("name") or "").strip()
    auto_mount = bool(data.get("auto_mount", True))
    if not device_id:
        return _error("invalid_payload", "Field 'device_id' is required", status=400)
    try:
        result = register_storage_device(device_id=device_id, name=name, auto_mount=auto_mount)
        return _ok(result)
    except NetControlError as exc:
        status = 500 if exc.code == "storage_config_write_failed" else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.post("/api/network/storage/ignore")
def api_network_storage_ignore():
    data = request.get_json(force=True, silent=True) or {}
    device_id = str(data.get("device_id") or "").strip()
    if not device_id:
        return _error("invalid_payload", "Field 'device_id' is required", status=400)
    try:
        return _ok(ignore_storage_device(device_id=device_id))
    except NetControlError as exc:
        status = 500 if exc.code == "storage_config_write_failed" else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.post("/api/network/storage/unignore")
def api_network_storage_unignore():
    data = request.get_json(force=True, silent=True) or {}
    device_id = str(data.get("device_id") or "").strip()
    if not device_id:
        return _error("invalid_payload", "Field 'device_id' is required", status=400)
    try:
        return _ok(unignore_storage_device(device_id=device_id))
    except NetControlError as exc:
        status = 500 if exc.code == "storage_config_write_failed" else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.post("/api/network/storage/remove")
def api_network_storage_remove():
    data = request.get_json(force=True, silent=True) or {}
    device_id = str(data.get("device_id") or "").strip()
    if not device_id:
        return _error("invalid_payload", "Field 'device_id' is required", status=400)
    try:
        return _ok(remove_storage_device(device_id=device_id))
    except NetControlError as exc:
        status = 500 if exc.code == "storage_config_write_failed" else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.post("/api/network/storage/mount")
def api_network_storage_mount():
    data = request.get_json(force=True, silent=True) or {}
    device_id = str(data.get("device_id") or "").strip()
    if not device_id:
        return _error("invalid_payload", "Field 'device_id' is required", status=400)
    try:
        return _ok(mount_storage_device(device_id=device_id))
    except NetControlError as exc:
        status = 500 if exc.code in ("storage_mount_failed", "execution_failed", "script_missing") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.post("/api/network/storage/unmount")
def api_network_storage_unmount():
    data = request.get_json(force=True, silent=True) or {}
    device_id = str(data.get("device_id") or "").strip()
    if not device_id:
        return _error("invalid_payload", "Field 'device_id' is required", status=400)
    try:
        return _ok(unmount_storage_device(device_id=device_id))
    except NetControlError as exc:
        status = 500 if exc.code in ("storage_unmount_failed", "execution_failed", "script_missing") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.post("/api/network/storage/format")
def api_network_storage_format():
    data = request.get_json(force=True, silent=True) or {}
    device_id = str(data.get("device_id") or "").strip()
    filesystem = str(data.get("filesystem") or "vfat").strip().lower()
    label = str(data.get("label") or "").strip()
    if not device_id:
        return _error("invalid_payload", "Field 'device_id' is required", status=400)
    try:
        return _ok(format_storage_device(device_id=device_id, filesystem=filesystem, label=label))
    except NetControlError as exc:
        status = 500 if exc.code in ("storage_format_failed", "execution_failed", "script_missing", "storage_config_write_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.post("/api/network/storage/toggle-enabled")
def api_network_storage_toggle_enabled():
    data = request.get_json(force=True, silent=True) or {}
    device_id = str(data.get("device_id") or "").strip()
    if "enabled" not in data:
        return _error("invalid_payload", "Fields 'device_id' and 'enabled' are required", status=400)
    enabled = bool(data.get("enabled"))
    if not device_id:
        return _error("invalid_payload", "Fields 'device_id' and 'enabled' are required", status=400)
    try:
        return _ok(set_storage_enabled(device_id=device_id, enabled=enabled))
    except NetControlError as exc:
        status = 500 if exc.code == "storage_config_write_failed" else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.post("/api/network/storage/toggle-automount")
def api_network_storage_toggle_automount():
    data = request.get_json(force=True, silent=True) or {}
    device_id = str(data.get("device_id") or "").strip()
    if "auto_mount" not in data:
        return _error("invalid_payload", "Fields 'device_id' and 'auto_mount' are required", status=400)
    auto_mount = bool(data.get("auto_mount"))
    if not device_id:
        return _error("invalid_payload", "Fields 'device_id' and 'auto_mount' are required", status=400)
    try:
        return _ok(set_storage_auto_mount(device_id=device_id, auto_mount=auto_mount))
    except NetControlError as exc:
        status = 500 if exc.code == "storage_config_write_failed" else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.get("/api/network/storage/file-manager/tree")
def api_network_storage_file_manager_tree():
    device_id = str(request.args.get("device_id") or "").strip()
    rel_path = str(request.args.get("path") or "").strip()
    if not device_id:
        return _error("invalid_payload", "Query field 'device_id' is required", status=400)
    try:
        return _ok(storage_fm.list_tree(device_id=device_id, relative_path=rel_path))
    except NetControlError as exc:
        status = 500 if exc.code in ("execution_failed",) else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.get("/api/network/storage/file-manager/list")
def api_network_storage_file_manager_list():
    device_id = str(request.args.get("device_id") or "").strip()
    rel_path = str(request.args.get("path") or "").strip()
    if not device_id:
        return _error("invalid_payload", "Query field 'device_id' is required", status=400)
    try:
        return _ok(storage_fm.list_directory(device_id=device_id, relative_path=rel_path))
    except NetControlError as exc:
        status = 500 if exc.code in ("execution_failed",) else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.get("/api/network/storage/file-manager/preview")
def api_network_storage_file_manager_preview():
    device_id = str(request.args.get("device_id") or "").strip()
    rel_path = str(request.args.get("path") or "").strip()
    if not device_id or not rel_path:
        return _error("invalid_payload", "Query fields 'device_id' and 'path' are required", status=400)
    try:
        return _ok(storage_fm.preview(device_id=device_id, relative_path=rel_path))
    except NetControlError as exc:
        status = 500 if exc.code in ("execution_failed",) else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.post("/api/network/storage/file-manager/delete")
def api_network_storage_file_manager_delete():
    data = request.get_json(force=True, silent=True) or {}
    device_id = str(data.get("device_id") or "").strip()
    selected_paths = data.get("paths")
    confirm_word = str(data.get("confirm_word") or "").strip()
    require_hard_confirm = bool(data.get("require_hard_confirm", False))
    try:
        confirm_count = int(data.get("confirm_count") or 0)
    except Exception:
        return _error("invalid_payload", "Field 'confirm_count' must be an integer", status=400)
    if not device_id or not isinstance(selected_paths, list):
        return _error("invalid_payload", "Fields 'device_id' and 'paths' are required", status=400)
    try:
        return _ok(
            storage_delete_service.delete_selected(
                device_id=device_id,
                selected_paths=selected_paths,
                confirm_word=confirm_word,
                confirm_count=confirm_count,
                require_hard_confirm=require_hard_confirm,
            )
        )
    except NetControlError as exc:
        status = 500 if exc.code in ("execution_failed",) else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.get("/api/network/storage/file-manager/file")
def api_network_storage_file_manager_file():
    device_id = str(request.args.get("device_id") or "").strip()
    rel_path = str(request.args.get("path") or "").strip()
    download = str(request.args.get("download") or "").strip().lower() in ("1", "true", "yes")
    if not device_id or not rel_path:
        return _error("invalid_payload", "Query fields 'device_id' and 'path' are required", status=400)
    try:
        file_path, mime = storage_fm.resolve_downloadable_file(
            device_id=device_id,
            relative_path=rel_path,
            enforce_preview_limit=not download,
        )
        return send_file(
            str(file_path),
            mimetype=mime,
            as_attachment=download,
            download_name=file_path.name if download else None,
            conditional=True,
        )
    except NetControlError as exc:
        status = 500 if exc.code in ("execution_failed",) else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.post("/api/network/storage/file-manager/mkdir")
def api_network_storage_file_manager_mkdir():
    data = request.get_json(force=True, silent=True) or {}
    device_id = str(data.get("device_id") or "").strip()
    rel_path = str(data.get("path") or "").strip()
    folder_name = str(data.get("name") or "").strip()
    if not device_id:
        return _error("invalid_payload", "Field 'device_id' is required", status=400)
    if not folder_name:
        return _error("invalid_payload", "Field 'name' is required", status=400)
    try:
        return _ok(storage_fm.create_folder(device_id=device_id, relative_path=rel_path, folder_name=folder_name))
    except NetControlError as exc:
        status = 500 if exc.code in ("execution_failed",) else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.post("/api/network/storage/file-manager/rename")
def api_network_storage_file_manager_rename():
    data = request.get_json(force=True, silent=True) or {}
    device_id = str(data.get("device_id") or "").strip()
    rel_path = str(data.get("path") or "").strip()
    new_name = str(data.get("new_name") or "").strip()
    if not device_id or not rel_path:
        return _error("invalid_payload", "Fields 'device_id' and 'path' are required", status=400)
    if not new_name:
        return _error("invalid_payload", "Field 'new_name' is required", status=400)
    try:
        return _ok(storage_fm.rename_entry(device_id=device_id, relative_path=rel_path, new_name=new_name))
    except NetControlError as exc:
        status = 500 if exc.code in ("execution_failed",) else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.post("/api/network/storage/file-manager/upload")
def api_network_storage_file_manager_upload():
    device_id = str(request.form.get("device_id") or "").strip()
    rel_path = str(request.form.get("path") or "").strip()
    files = request.files.getlist("files")
    if not files:
        files = request.files.getlist("files[]")
    if not device_id:
        return _error("invalid_payload", "Form field 'device_id' is required", status=400)
    if not files:
        return _error("invalid_payload", "At least one file is required (files[])", status=400)
    try:
        return _ok(storage_fm.upload_files(device_id=device_id, relative_path=rel_path, files=files))
    except NetControlError as exc:
        status = 500 if exc.code in ("execution_failed",) else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)
    except Exception as exc:
        return _error("storage_upload_failed_internal", "Upload failed", status=500, detail=str(exc))


@bp_network.post("/api/network/wps")
def api_network_wps():
    data = request.get_json(force=True, silent=True) or {}
    ifname = (data.get("ifname") or "wlan0").strip()
    target_bssid = (data.get("target_bssid") or "").strip()
    target_ssid = (data.get("target_ssid") or "").strip()
    async_safe = bool(data.get("async_safe", False))
    log_event("wps", "WPS start requested", data={"ifname": ifname, "target_ssid": target_ssid, "target_bssid": target_bssid})
    set_wps_state(
        {
            "active": True,
            "started_at_ts": time.time(),
            "ifname": ifname,
            "target_ssid": target_ssid,
            "target_bssid": target_bssid,
        }
    )

    ap_active = False
    try:
        ap_active = bool(get_ap_status(ifname=ifname).get("active"))
    except NetControlError:
        ap_active = False

    if async_safe or (_is_ap_client_request() and ap_active):
        threading.Thread(
            target=_run_wps_start_async,
            args=(ifname, target_bssid, target_ssid),
            daemon=True,
            name="wps-start-async",
        ).start()
        return (
            jsonify(
                ok=True,
                success=True,
                message="WPS wird gestartet.",
                details="AP-safe async mode",
                hint="Bitte jetzt am Router die WPS-Taste drücken; Status wird laufend aktualisiert.",
                data={
                    "iface": ifname,
                    "code": "wps_start_async",
                    "async": True,
                    "ap_disconnect_expected": bool(ap_active),
                    "reconnect_hint": "Bei AP-Betrieb trennt sich die Seite kurz, sobald wlan0 auf das Ziel-WLAN umschaltet.",
                },
            ),
            200,
        )

    try:
        result = start_wps(ifname=ifname, target_bssid=target_bssid, target_ssid=target_ssid)
        log_event("wps", result.get("message", "WPS started"), data={"iface": ifname, "code": result.get("code", "ok")})
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
                    log_event("wps", "WPS connected after trigger error", level="warning", data={"iface": ifname, "detail": exc.detail})
                    set_wps_state({"active": False, "ifname": ifname, "finished_at_ts": time.time(), "result": "connected"})
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
        log_event("wps", exc.message, level="error", data={"iface": ifname, "code": exc.code, "detail": exc.detail})
        set_wps_state({"active": False, "ifname": ifname, "finished_at_ts": time.time(), "result": exc.code})
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


@bp_network.post("/api/network/wifi/wps/start")
def api_network_wifi_wps_start():
    return api_network_wps()


@bp_network.get("/api/network/wifi/wps/status")
def api_network_wifi_wps_status():
    ifname = (request.args.get("ifname") or "wlan0").strip()
    try:
        status = get_wifi_status(ifname=ifname)
    except NetControlError as exc:
        return _error(exc.code, exc.message, status=400, detail=exc.detail)
    wps_state = get_wps_state()
    phase = _wps_phase_from_status(status, wps_state)
    if phase["phase"] == "dhcp_request" and bool(wps_state.get("active")):
        try:
            dhcp = wifi_request_dhcp(ifname=ifname)
            status["ip"] = dhcp.get("ip", status.get("ip", ""))
            if status["ip"]:
                phase["phase"] = "connected"
                phase["phase_message"] = "WLAN verbunden und IP vorhanden."
                set_wps_state({"active": False, "ifname": ifname, "finished_at_ts": time.time(), "result": "connected"})
                log_event("wps", "DHCP lease acquired after WPS", data={"ifname": ifname, "ip": status["ip"]})
        except NetControlError as exc:
            log_event("wps", "DHCP request after WPS failed", level="warning", data={"ifname": ifname, "detail": exc.detail or exc.message})
    if phase["phase"] in ("connected", "timeout"):
        set_wps_state({"active": False, "ifname": ifname, "finished_at_ts": time.time(), "result": phase["phase"]})
    if phase["phase"] == "connected":
        connected_ssid = (status.get("ssid") or "").strip()
        if _is_ap_ssid(connected_ssid, ifname=ifname):
            # Do not ever persist/select our own AP SSID as client uplink target.
            set_wps_state({"active": False, "ifname": ifname, "finished_at_ts": time.time(), "result": "self_ap_ignored"})
            return _ok(
                {
                    "ifname": ifname,
                    "wps": {
                        **phase,
                        "phase": "idle",
                        "phase_message": "Eigenes AP-Netz erkannt und ignoriert.",
                        "target_ssid": wps_state.get("target_ssid", ""),
                        "target_bssid": wps_state.get("target_bssid", ""),
                    },
                    "wifi": status,
                    "ap_fallback": {"attempted": False, "disabled": False, "was_enabled": False, "ifname": ifname, "reason": "self_ap_ignored"},
                }
            )
        ap_fallback = _disable_ap_after_wifi_uplink(ifname=ifname, reason="wps_connected")
        if connected_ssid:
            try:
                wifi_profile_set(connected_ssid, PREFERRED_WIFI_PRIORITY, True)
            except NetControlError as exc:
                log_event("wps", "Could not set autoconnect priority after WPS", level="warning", data={"ssid": connected_ssid, "detail": exc.detail or exc.message})
            try:
                cfg = ensure_config()
                profiles = _norm_profiles(cfg)
                found = False
                for item in profiles:
                    if item["ssid"] == connected_ssid:
                        item["priority"] = max(int(item.get("priority") or 0), PREFERRED_WIFI_PRIORITY)
                        item["autoconnect"] = True
                        found = True
                        break
                if not found:
                    profiles.append({"ssid": connected_ssid, "priority": PREFERRED_WIFI_PRIORITY, "autoconnect": True})
                ok, err = _save_wifi_profiles(cfg, profiles, preferred=connected_ssid, last_ssid=connected_ssid)
                if not ok:
                    log_event("wps", "Could not persist connected SSID after WPS", level="warning", data={"ssid": connected_ssid, "detail": err})
            except Exception as exc:
                log_event("wps", "Post-WPS config update failed", level="warning", data={"ssid": connected_ssid, "detail": str(exc)})
    else:
        ap_fallback = {"attempted": False, "disabled": False, "was_enabled": False, "ifname": ifname, "reason": ""}
    return _ok(
        {
            "ifname": ifname,
            "wps": {**phase, "target_ssid": wps_state.get("target_ssid", ""), "target_bssid": wps_state.get("target_bssid", "")},
            "wifi": status,
            "ap_fallback": ap_fallback,
        }
    )


@bp_network.get("/api/network/wifi/status")
def api_network_wifi_status():
    ifname = (request.args.get("ifname") or "wlan0").strip()
    try:
        status = get_wifi_status(ifname=ifname)
        return _ok(status)
    except NetControlError as exc:
        return _error(exc.code, exc.message, status=400, detail=exc.detail)


@bp_network.get("/api/wifi/scan")
def api_wifi_scan():
    ifname = (request.args.get("ifname") or "wlan0").strip()
    try:
        payload = wifi_scan(ifname=ifname)
        ap_ssid = _current_ap_ssid(ifname=ifname)
        networks = payload.get("networks") if isinstance(payload.get("networks"), list) else []
        if ap_ssid:
            payload["networks"] = [
                item for item in networks if str((item or {}).get("ssid") or "").strip().casefold() != ap_ssid.casefold()
            ]
        return _ok(payload)
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.get("/api/network/wifi/scan")
def api_network_wifi_scan():
    return api_wifi_scan()


@bp_network.post("/api/wifi/connect")
def api_wifi_connect():
    data = request.get_json(force=True, silent=True) or {}
    ssid = (data.get("ssid") or "").strip()
    password = data.get("password") or ""
    hidden = bool(data.get("hidden", False))
    ifname = (data.get("ifname") or "wlan0").strip()
    if not ssid:
        return _error("invalid_payload", "Field 'ssid' is required", status=400)
    if _is_ap_ssid(ssid, ifname=ifname):
        return _error("forbidden_ssid", "Das eigene AP-Netz darf nicht als Client-WLAN verwendet werden.", status=409)
    pre_switch = _prepare_client_uplink_switch(ifname=ifname, reason="wifi_connect_pre")
    try:
        result = wifi_connect(ssid=ssid, password=password, ifname=ifname, hidden=hidden)
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
    log_event("wifi", "Connected to Wi-Fi network", data={"ssid": ssid, "ifname": ifname})
    ap_fallback = _disable_ap_after_wifi_uplink(ifname=ifname, reason="wifi_connect")
    return _ok({**result, "persisted": True, "ap_pre_switch": pre_switch, "ap_fallback": ap_fallback})


@bp_network.post("/api/network/wifi/connect")
def api_network_wifi_connect():
    return api_wifi_connect()


@bp_network.get("/api/wifi/profiles")
def api_wifi_profiles():
    cfg = ensure_config()
    _purge_ap_ssid_profile_if_needed(cfg)
    profiles_cfg = _norm_profiles(cfg)
    ap_ssid = _current_ap_ssid()
    try:
        nm_profiles = wifi_profiles_list().get("profiles", [])
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)
    if ap_ssid:
        nm_profiles = [item for item in nm_profiles if str(item.get("name") or "").strip().casefold() != ap_ssid.casefold()]
        profiles_cfg = [item for item in profiles_cfg if str(item.get("ssid") or "").strip().casefold() != ap_ssid.casefold()]
    preferred_ssid = (cfg.get("preferred_wifi") or "")
    last_wifi_ssid = (cfg.get("last_wifi_ssid") or "")
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
                "source": "config+nm" if nm_item else "config",
            }
        )
    known = {item["ssid"] for item in profiles_cfg}
    unmanaged = [item for item in nm_profiles if item.get("name") not in known]
    profiles = _merged_profiles(profiles_cfg, nm_profiles, preferred_ssid, last_wifi_ssid)
    return _ok(
        {
            "configured": configured,
            "unmanaged": unmanaged,
            "profiles": profiles,
            "preferred_ssid": preferred_ssid,
            "last_wifi_ssid": last_wifi_ssid,
        }
    )


@bp_network.get("/api/network/wifi/saved")
def api_network_wifi_saved():
    return api_wifi_profiles()


@bp_network.post("/api/wifi/profiles/add")
def api_wifi_profiles_add():
    data = request.get_json(force=True, silent=True) or {}
    ssid = (data.get("ssid") or "").strip()
    password = data.get("password") or ""
    hidden = bool(data.get("hidden", False))
    ifname = (data.get("ifname") or "wlan0").strip()
    try:
        priority = int(data.get("priority") or NONPREFERRED_WIFI_PRIORITY)
    except Exception:
        priority = NONPREFERRED_WIFI_PRIORITY
    autoconnect = bool(data.get("autoconnect", True))
    if not ssid:
        return _error("invalid_payload", "Field 'ssid' is required", status=400)
    if _is_ap_ssid(ssid, ifname=ifname):
        return _error("forbidden_ssid", "Das eigene AP-Netz darf nicht als Client-WLAN gespeichert werden.", status=409)

    connect_error = ""
    try:
        wifi_connect(ssid=ssid, password=password, ifname=ifname, hidden=hidden)
    except NetControlError as exc:
        connect_error = exc.detail or exc.message

    profile_sync_warning = ""
    try:
        wifi_profile_set(ssid=ssid, priority=priority, autoconnect=autoconnect)
    except NetControlError as exc:
        detail = exc.detail or exc.message
        if _is_unknown_connection_detail(detail):
            profile_sync_warning = "Profil ist im Portal gespeichert, aber in NetworkManager noch nicht vorhanden (SSID derzeit evtl. nicht erreichbar)."
            log_event("wifi", "Wi-Fi profile saved in portal config without NM profile", level="warning", data={"ssid": ssid, "detail": detail})
        else:
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
    if profile_sync_warning:
        payload["warning"] = f"{payload.get('warning', '')} {profile_sync_warning}".strip()
        payload["nm_profile_synced"] = False
    else:
        payload["nm_profile_synced"] = True
    return _ok(payload)


@bp_network.post("/api/wifi/profiles/delete")
def api_wifi_profiles_delete():
    data = request.get_json(force=True, silent=True) or {}
    ssid = (data.get("ssid") or "").strip()
    uuid = (data.get("uuid") or "").strip()
    if not ssid:
        return _error("invalid_payload", "Field 'ssid' is required", status=400)
    try:
        current_ssid = ""
        try:
            current_ssid = (get_wifi_status(ifname="wlan0").get("ssid") or "").strip()
        except NetControlError:
            current_ssid = ""
        wifi_profile_delete(ssid, uuid=uuid)
        if current_ssid and current_ssid == ssid:
            try:
                wifi_disconnect(ifname="wlan0")
            except NetControlError:
                pass
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
    log_event("wifi", "Removed Wi-Fi profile", data={"ssid": ssid})
    return _ok({"ssid": ssid, "uuid": uuid})


@bp_network.post("/api/network/wifi/remove")
def api_network_wifi_remove():
    return api_wifi_profiles_delete()


@bp_network.post("/api/wifi/profiles/prefer")
def api_wifi_profiles_prefer():
    data = request.get_json(force=True, silent=True) or {}
    ssid = (data.get("ssid") or "").strip()
    if not ssid:
        return _error("invalid_payload", "Field 'ssid' is required", status=400)
    if _is_ap_ssid(ssid):
        return _error("forbidden_ssid", "Das eigene AP-Netz darf nicht als bevorzugtes Client-WLAN gesetzt werden.", status=409)

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

    profile_sync_warning = ""
    try:
        wifi_profile_set(ssid=ssid, priority=PREFERRED_WIFI_PRIORITY, autoconnect=True)
    except NetControlError as exc:
        detail = exc.detail or exc.message
        if _is_unknown_connection_detail(detail):
            profile_sync_warning = "Bevorzugung im Portal gespeichert; NetworkManager-Profil fehlt aktuell."
            log_event("wifi", "Preferred Wi-Fi saved without NM profile", level="warning", data={"ssid": ssid, "detail": detail})
        else:
            status = 500 if exc.code in ("script_missing", "execution_failed") else 400
            return _error(exc.code, exc.message, status=status, detail=exc.detail)

    ok, err = _save_wifi_profiles(cfg, profiles, preferred=ssid)
    if not ok:
        return _error("config_write_failed", "Preferred profile updated but config write failed", status=500, detail=err)
    return _ok({"preferred_ssid": ssid, "profiles": profiles, "nm_profile_synced": not bool(profile_sync_warning), "warning": profile_sync_warning})


@bp_network.post("/api/wifi/profiles/up")
def api_wifi_profiles_up():
    data = request.get_json(force=True, silent=True) or {}
    ssid = (data.get("ssid") or "").strip()
    uuid = (data.get("uuid") or "").strip()
    if not ssid:
        return _error("invalid_payload", "Field 'ssid' is required", status=400)
    if _is_ap_ssid(ssid):
        return _error("forbidden_ssid", "Das eigene AP-Netz darf nicht als Client-WLAN verbunden werden.", status=409)
    pre_switch = _prepare_client_uplink_switch(ifname="wlan0", reason="wifi_profile_up_pre")
    try:
        result = wifi_profile_up(ssid, uuid=uuid)
    except NetControlError as exc:
        if exc.code == "wifi_secrets_required":
            return _error(
                "wifi_secrets_required",
                "Profil kann nicht direkt verbunden werden: Passwort fehlt oder WPS-Kopplung notwendig.",
                status=409,
                detail="Bitte entweder Passwort im Profil hinterlegen (manuell hinzufügen) oder WPS für dieses WLAN starten.",
            )
        status = 500 if exc.code in ("script_missing", "execution_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)
    cfg = ensure_config()
    ok, err = _save_wifi_profiles(cfg, _norm_profiles(cfg), last_ssid=ssid)
    if not ok:
        return _error("config_write_failed", "Profile activated but state persistence failed", status=500, detail=err)
    log_event("wifi", "Activated Wi-Fi profile", data={"ssid": ssid})
    ap_fallback = _disable_ap_after_wifi_uplink(ifname="wlan0", reason="wifi_profile_up")
    return _ok({**result, "ap_pre_switch": pre_switch, "ap_fallback": ap_fallback})


@bp_network.post("/api/network/wifi/select")
def api_network_wifi_select():
    return api_wifi_profiles_up()


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
    pre_switch = _prepare_client_uplink_switch(ifname="wlan0", reason="wifi_profiles_apply_pre")
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
    log_event("wifi", "Applied Wi-Fi profiles", data={"connected_ssid": connected_ssid, "profiles": len(profiles)})
    ap_fallback = _disable_ap_after_wifi_uplink(ifname="wlan0", reason="wifi_profiles_apply")
    return _ok({"connected_ssid": connected_ssid, "logs": logs, "profiles": profiles, "ap_pre_switch": pre_switch, "ap_fallback": ap_fallback})


@bp_network.post("/api/network/wifi/toggle")
def api_network_wifi_toggle_alias():
    return api_network_wifi_toggle()


@bp_network.post("/api/network/wifi/disconnect")
def api_network_wifi_disconnect():
    data = request.get_json(force=True, silent=True) or {}
    ifname = (data.get("ifname") or "wlan0").strip()
    try:
        result = wifi_disconnect(ifname=ifname)
        log_event("wifi", "Disconnected Wi-Fi interface", data={"ifname": ifname})
        return _ok(result)
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.get("/api/network/wifi/logs")
def api_network_wifi_logs():
    try:
        limit = int(request.args.get("limit", "120"))
    except Exception:
        limit = 120
    limit = max(1, min(limit, 500))
    events = read_events(limit=limit)
    return _ok({"events": events, "count": len(events)})


@bp_network.post("/api/system/tailscale/disable-dns")
def api_system_tailscale_disable_dns():
    try:
        result = disable_tailscale_dns_override()
        return _ok(result)
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.post("/api/system/settings")
def api_system_settings_update():
    data = request.get_json(force=True, silent=True) or {}
    if "storage_delete_hardcore_mode" not in data:
        return _error("invalid_payload", "Field 'storage_delete_hardcore_mode' is required", status=400)
    value = data.get("storage_delete_hardcore_mode")
    if not isinstance(value, bool):
        return _error("invalid_payload", "Field 'storage_delete_hardcore_mode' must be a boolean", status=400)

    cfg = ensure_config()
    cfg["storage_delete_hardcore_mode"] = bool(value)
    cfg["updated_at"] = utc_now()
    ok, err = write_json(CONFIG_PATH, cfg, mode=0o600)
    if not ok:
        return _error("config_write_failed", "Could not persist system settings", status=500, detail=err)
    return _ok(
        {
            "storage_delete_hardcore_mode": bool(value),
            "updated_at": cfg.get("updated_at", ""),
            "message": "System settings updated",
        }
    )


@bp_network.get("/api/network/security/sentinels/status")
def api_network_security_sentinels_status():
    cfg = ensure_config()
    settings = cfg.get("sentinel_settings") if isinstance(cfg.get("sentinel_settings"), dict) else {}
    webhook_url = str((settings or {}).get("webhook_url") or "")
    try:
        payload = sentinels_status(webhook_url=webhook_url)
        return _ok(payload)
    except SentinelManagerError as exc:
        status = 500 if exc.code in ("execution_failed", "source_not_found", "command_not_found") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.post("/api/network/security/sentinels/webhook")
def api_network_security_sentinels_webhook():
    data = request.get_json(force=True, silent=True) or {}
    webhook_url = str(data.get("webhook_url") or "").strip()
    if not webhook_url:
        return _error("invalid_payload", "Field 'webhook_url' is required", status=400)
    if not webhook_url.lower().startswith(("http://", "https://")):
        return _error("invalid_payload", "Field 'webhook_url' must start with http:// or https://", status=400)

    cfg = ensure_config()
    settings = cfg.get("sentinel_settings") if isinstance(cfg.get("sentinel_settings"), dict) else {}
    settings["webhook_url"] = webhook_url
    settings["updated_at"] = utc_now()
    cfg["sentinel_settings"] = settings
    cfg["updated_at"] = utc_now()
    ok, err = write_json(CONFIG_PATH, cfg, mode=0o600)
    if not ok:
        return _error("config_write_failed", "Could not persist sentinel webhook URL", status=500, detail=err)

    try:
        payload = sentinels_status(webhook_url=webhook_url)
        return _ok({"message": "Webhook URL gespeichert.", **payload})
    except SentinelManagerError as exc:
        status = 500 if exc.code in ("execution_failed", "source_not_found", "command_not_found") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.post("/api/network/security/sentinels/install")
def api_network_security_sentinels_install():
    data = request.get_json(force=True, silent=True) or {}
    slug = str(data.get("slug") or "").strip()
    if not slug:
        return _error("invalid_payload", "Field 'slug' is required", status=400)

    cfg = ensure_config()
    settings = cfg.get("sentinel_settings") if isinstance(cfg.get("sentinel_settings"), dict) else {}
    webhook_url = str((settings or {}).get("webhook_url") or "").strip()
    if not webhook_url:
        return _error("invalid_payload", "Please save a webhook URL first.", status=400)

    try:
        payload = install_sentinel_module(slug=slug, webhook_url=webhook_url)
        return _ok({"message": f"Sentinel '{slug}' installiert.", **payload.get("status", {})})
    except SentinelManagerError as exc:
        status = 500 if exc.code in ("execution_failed", "source_not_found", "command_not_found") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.post("/api/network/security/sentinels/uninstall")
def api_network_security_sentinels_uninstall():
    data = request.get_json(force=True, silent=True) or {}
    slug = str(data.get("slug") or "").strip()
    if not slug:
        return _error("invalid_payload", "Field 'slug' is required", status=400)

    cfg = ensure_config()
    settings = cfg.get("sentinel_settings") if isinstance(cfg.get("sentinel_settings"), dict) else {}
    webhook_url = str((settings or {}).get("webhook_url") or "").strip()

    try:
        payload = uninstall_sentinel_module(slug=slug, webhook_url=webhook_url)
        return _ok({"message": f"Sentinel '{slug}' entfernt.", **payload.get("status", {})})
    except SentinelManagerError as exc:
        status = 500 if exc.code in ("execution_failed", "source_not_found", "command_not_found") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.post("/api/system/hostname/preview")
def api_system_hostname_preview():
    data = request.get_json(force=True, silent=True) or {}
    new_hostname = str(data.get("hostname") or "").strip()
    if not new_hostname:
        return _error("invalid_payload", "Field 'hostname' is required", status=400)
    ap_profile = str(data.get("ap_profile") or "jm-hotspot").strip() or "jm-hotspot"
    try:
        preview = hostname_rename_preview(new_hostname=new_hostname, ap_profile=ap_profile)
        return _ok(preview)
    except NetControlError as exc:
        return _error(exc.code, exc.message, status=400, detail=exc.detail)


@bp_network.post("/api/system/hostname/rename")
def api_system_hostname_rename():
    data = request.get_json(force=True, silent=True) or {}
    new_hostname = str(data.get("hostname") or "").strip()
    if not new_hostname:
        return _error("invalid_payload", "Field 'hostname' is required", status=400)
    confirm_phrase = str(data.get("confirm_phrase") or "").strip()
    if confirm_phrase != "Hostname Ändern":
        return _error("invalid_confirmation", "Confirmation phrase mismatch", status=400)
    ap_profile = str(data.get("ap_profile") or "jm-hotspot").strip() or "jm-hotspot"

    try:
        result = apply_hostname_rename(new_hostname=new_hostname, ap_profile=ap_profile)
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed", "hostname_rename_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)

    cfg = ensure_config()
    dev = ensure_device()
    fp = collect_fingerprint()
    mode = "play" if (cfg.get("selected_stream_slug") or "").strip() else "setup"
    state, _ = update_state(cfg, dev, fp, mode=mode, message="hostname renamed")
    result["state"] = state
    result["fingerprint"] = {
        "hostname": fp.get("hostname"),
        "machine_id": fp.get("machine_id"),
        "pi_serial": ((fp.get("cpu") or {}).get("serial") if isinstance(fp.get("cpu"), dict) else ""),
    }
    log_event(
        "system",
        "Hostname updated",
        data={
            "old_hostname": result.get("old_hostname", ""),
            "new_hostname": result.get("new_hostname", ""),
            "ap_ssid": result.get("ap_ssid", ""),
            "bt_name": result.get("bt_name", ""),
        },
    )
    return _ok(result)


@bp_network.post("/api/system/portal/update")
def api_system_portal_update():
    try:
        cfg = ensure_config()
        source = str(cfg.get("portal_update_url") or "").strip()
        result = portal_update(service_name="device-portal.service", update_source=source)
        log_event("system", "Portal update triggered", data={"git_status": result.get("git_status", "unknown")})
        return _ok(result)
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed", "portal_update_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.get("/api/system/portal/update/status")
def api_system_portal_update_status():
    job_id = (request.args.get("job_id") or "").strip()
    try:
        payload = portal_update_status(job_id=job_id)
        return _ok(payload)
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed", "update_state_read_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.post("/api/system/power")
def api_system_power():
    data = request.get_json(force=True, silent=True) or {}
    action = str(data.get("action") or "").strip().lower()
    if action not in {"shutdown", "reboot"}:
        return _error("invalid_payload", "Field 'action' must be 'shutdown' or 'reboot'", status=400)
    try:
        result = system_power_action(action=action)
        log_event("system", "System power action requested", data={"action": action})
        return _ok(result)
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed", "system_power_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_network.post("/api/system/portal/restart")
def api_system_portal_restart():
    try:
        result = restart_portal_service(service_name="device-portal.service")
        log_event("system", "Portal service restart requested", data={"service_name": result.get("service_name", "device-portal.service")})
        return _ok(result)
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed", "portal_restart_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)
