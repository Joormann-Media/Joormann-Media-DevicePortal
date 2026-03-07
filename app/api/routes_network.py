from __future__ import annotations

import time

from flask import Blueprint, jsonify, request, send_file

from app.core.config import ensure_config
from app.core.device import ensure_device
from app.core.display import DISPLAY_MOUNT_ORIENTATIONS, get_display_snapshot, normalize_mount_orientation, update_display_config
from app.core.fingerprint import collect_fingerprint
from app.core.jsonio import write_json
from app.core.network_events import get_wps_state, log_event, read_events, set_wps_state
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
    restart_portal_service,
    set_ap_enabled,
    get_bluetooth_status,
    set_bluetooth_enabled,
    set_bluetooth_runtime_settings,
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
from app.core.paths import CONFIG_PATH
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

bp_network = Blueprint("network", __name__)
storage_fm = StorageFileManagerService()
storage_delete_service = StorageDeleteService(storage_fm)


def _ok(data: dict, status: int = 200):
    message = data.get("message") if isinstance(data, dict) else ""
    return jsonify(ok=True, success=True, message=message or "ok", data=data, error_code=""), status


def _error(code: str, message: str, status: int = 400, detail: str = ""):
    payload = {"code": code, "message": message}
    if detail:
        payload["detail"] = detail
    return jsonify(ok=False, success=False, message=message, data={}, error_code=code, error=payload), status


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


@bp_network.get("/api/network/info")
def api_network_info():
    try:
        info = get_network_info()
        return _ok(info)
    except NetControlError as exc:
        http_status = 500 if exc.code in ("script_missing", "execution_failed") else 400
        return _error(exc.code, exc.message, status=http_status, detail=exc.detail)


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
    return _ok(
        {
            "saved": True,
            "connector": connector,
            "display_config": updated_item,
            "display": snapshot,
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
        log_event("ap", "AP hotspot toggled", data={"enabled": result.get("enabled", False), "ifname": ifname, "profile": profile})
        return _ok(result)
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
    return _ok(
        {
            "ifname": ifname,
            "wps": {**phase, "target_ssid": wps_state.get("target_ssid", ""), "target_bssid": wps_state.get("target_bssid", "")},
            "wifi": status,
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
        return _ok(wifi_scan(ifname=ifname))
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
    return _ok({**result, "persisted": True})


@bp_network.post("/api/network/wifi/connect")
def api_network_wifi_connect():
    return api_wifi_connect()


@bp_network.get("/api/wifi/profiles")
def api_wifi_profiles():
    cfg = ensure_config()
    profiles_cfg = _norm_profiles(cfg)
    try:
        nm_profiles = wifi_profiles_list().get("profiles", [])
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)
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

    connect_error = ""
    try:
        wifi_connect(ssid=ssid, password=password, ifname=ifname, hidden=hidden)
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
    uuid = (data.get("uuid") or "").strip()
    if not ssid:
        return _error("invalid_payload", "Field 'ssid' is required", status=400)
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
    return _ok(result)


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
    return _ok({"connected_ssid": connected_ssid, "logs": logs, "profiles": profiles})


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
        result = portal_update(service_name="device-portal.service")
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
