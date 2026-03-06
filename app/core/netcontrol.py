from __future__ import annotations

import json
import os
import subprocess
import getpass
import re
from pathlib import Path

from app.core.paths import ASSET_DIR

class NetControlError(Exception):
    def __init__(self, code: str, message: str, detail: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_NET_SCRIPTS = REPO_ROOT / "scripts" / "net"
DEPLOY_NET_SCRIPTS = Path(os.getenv("NETCONTROL_BIN_DIR", "/opt/deviceportal/bin"))

ALLOWED_LAN_INTERFACES = {"eth0"}
ALLOWED_WIFI_INTERFACES = {"wlan0"}
DEFAULT_WIFI_IFACE = "wlan0"
DEFAULT_AP_PROFILE = os.getenv("AP_PROFILE_NAME", "jm-hotspot")
PREFERRED_WIFI_PRIORITY = 999
NONPREFERRED_WIFI_PRIORITY = 100
MAX_UPDATE_LOG_BYTES = 64 * 1024


def _update_dir() -> Path:
    path = Path(ASSET_DIR) / "updates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _is_valid_job_id(job_id: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._-]{1,96}", job_id or ""))


def _tail_file(path: Path, max_bytes: int = MAX_UPDATE_LOG_BYTES) -> str:
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            start = max(0, size - max_bytes)
            fh.seek(start)
            raw = fh.read().decode("utf-8", errors="replace")
        return raw.strip()
    except Exception:
        return ""


def _service_user_from_systemd(service_name: str) -> str:
    unit = (service_name or "").strip()
    if not unit:
        return ""
    try:
        out = subprocess.check_output(
            ["systemctl", "show", unit, "--property=User", "--value"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return ""
    if not out or out in ("", "root"):
        return ""
    return out


def _candidate_script_paths(script_name: str) -> list[Path]:
    return [
        DEPLOY_NET_SCRIPTS / script_name,
        REPO_NET_SCRIPTS / script_name,
    ]


def _resolve_script(script_name: str) -> str:
    for path in _candidate_script_paths(script_name):
        if path.exists() and path.is_file():
            return str(path.resolve())
    raise NetControlError(
        code="script_missing",
        message=f"Missing netcontrol script: {script_name}",
        detail=f"searched in {DEPLOY_NET_SCRIPTS} and {REPO_NET_SCRIPTS}",
    )


def _run_script(script_name: str, args: list[str], timeout: int, use_sudo: bool) -> tuple[int, str, str]:
    script_path = _resolve_script(script_name)
    cmd: list[str] = [script_path] + args
    if use_sudo:
        cmd = ["sudo", "-n"] + cmd
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise NetControlError(code="command_not_found", message="Required runtime command missing", detail=str(exc))
    except subprocess.TimeoutExpired:
        raise NetControlError(code="timeout", message=f"Command timed out after {timeout}s")
    except Exception as exc:
        raise NetControlError(code="execution_failed", message="Could not execute netcontrol command", detail=str(exc))
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def _parse_kv_output(raw: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in (raw or "").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        parsed[key] = value.strip()
    return parsed


def _is_missing_wifi_secret_error(detail: str) -> bool:
    text = (detail or "").lower()
    if not text:
        return False
    markers = (
        "secrets were required",
        "secret agent",
        "password",
        "passwort",
        "wps button",
        "geheimdaten",
        "psk",
        "--ask",
    )
    return any(marker in text for marker in markers)


def _split_nmcli_escaped(line: str) -> list[str]:
    fields: list[str] = []
    buf: list[str] = []
    escape = False
    for ch in line:
        if escape:
            buf.append(ch)
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == ":":
            fields.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    fields.append("".join(buf))
    return fields


def get_network_info() -> dict:
    rc, out, err = _run_script("network_info.sh", [], timeout=8, use_sudo=False)
    if rc != 0:
        raise NetControlError(code="network_info_failed", message="Failed to read network status", detail=err or out)
    try:
        payload = json.loads(out) if out else {}
    except Exception as exc:
        raise NetControlError(code="network_info_invalid_json", message="Network status script returned invalid JSON", detail=str(exc))
    if not isinstance(payload, dict):
        raise NetControlError(code="network_info_invalid", message="Network status payload must be a JSON object")
    return payload


def set_wifi_enabled(enabled: bool) -> dict:
    state_arg = "on" if enabled else "off"
    rc, out, err = _run_script("wifi_toggle.sh", [state_arg], timeout=10, use_sudo=True)
    if rc != 0:
        raise NetControlError(code="wifi_toggle_failed", message="Failed to toggle Wi-Fi", detail=err or out)
    return {"enabled": enabled, "stdout": out}


def set_bluetooth_enabled(enabled: bool) -> dict:
    state_arg = "on" if enabled else "off"
    rc, out, err = _run_script("bluetooth_toggle.sh", [state_arg], timeout=10, use_sudo=True)
    if rc != 0:
        raise NetControlError(code="bluetooth_toggle_failed", message="Failed to toggle Bluetooth", detail=err or out)
    return {"enabled": enabled, "stdout": out}


def set_lan_enabled(enabled: bool, ifname: str = "eth0") -> dict:
    iface = (ifname or "eth0").strip()
    if iface not in ALLOWED_LAN_INTERFACES:
        raise NetControlError(code="invalid_interface", message=f"Interface {iface!r} is not allowed")
    state_arg = "up" if enabled else "down"
    rc, out, err = _run_script("lan_toggle.sh", [state_arg, iface], timeout=10, use_sudo=True)
    if rc != 0:
        raise NetControlError(code="lan_toggle_failed", message="Failed to toggle LAN interface", detail=err or out)
    return {"ifname": iface, "enabled": enabled, "stdout": out}


def _parse_wifi_scan_output(raw: str) -> list[dict]:
    networks: list[dict] = []
    for line in (raw or "").splitlines():
        if not line.strip():
            continue
        parts = _split_nmcli_escaped(line)
        in_use_field = parts[0] if len(parts) > 0 else ""
        in_use = in_use_field.strip() in ("*", "yes")
        if len(parts) >= 5:
            # Expected: IN-USE,SSID,BSSID,SIGNAL,SECURITY
            ssid = parts[1]
            bssid = parts[2]
            signal = parts[3]
            security = ":".join(parts[4:])
        else:
            ssid = parts[1] if len(parts) > 1 else ""
            signal = ""
            security = ""
            bssid = ""
        ssid = (ssid or "").strip() or "<hidden>"
        try:
            signal_num = int((signal or "0").strip())
        except Exception:
            signal_num = 0
        networks.append(
            {
                "in_use": bool(in_use),
                "ssid": ssid,
                "bssid": (bssid or "").strip(),
                "signal": signal_num,
                "security": (security or "").strip(),
            }
        )
    networks.sort(key=lambda item: int(item.get("signal") or 0), reverse=True)
    return networks


def _parse_wifi_profiles_output(raw: str) -> list[dict]:
    profiles: list[dict] = []
    for line in (raw or "").splitlines():
        if not line.strip():
            continue
        parts = _split_nmcli_escaped(line)
        if len(parts) < 5:
            continue
        # Expected: NAME,UUID,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY
        name = parts[0].strip()
        uuid = parts[1].strip()
        conn_type = parts[2].strip()
        autoconnect = parts[3].strip().lower() == "yes"
        prio_raw = parts[4].strip()
        if conn_type not in ("wifi", "802-11-wireless"):
            continue
        try:
            priority = int(prio_raw)
        except Exception:
            priority = 0
        profiles.append(
            {
                "name": name,
                "uuid": uuid,
                "type": conn_type,
                "autoconnect": autoconnect,
                "priority": priority,
            }
        )
    return profiles


def wifi_scan(ifname: str = DEFAULT_WIFI_IFACE) -> dict:
    iface = (ifname or DEFAULT_WIFI_IFACE).strip() or DEFAULT_WIFI_IFACE
    rc, out, err = _run_script("wifi_profile.sh", ["scan", iface], timeout=20, use_sudo=True)
    if rc != 0:
        raise NetControlError(code="wifi_scan_failed", message="Failed to scan Wi-Fi networks", detail=err or out)
    return {"ifname": iface, "networks": _parse_wifi_scan_output(out)}


def wifi_connect(ssid: str, password: str = "", ifname: str = DEFAULT_WIFI_IFACE, hidden: bool = False) -> dict:
    ssid = (ssid or "").strip()
    iface = (ifname or DEFAULT_WIFI_IFACE).strip() or DEFAULT_WIFI_IFACE
    if not ssid:
        raise NetControlError(code="invalid_payload", message="Missing ssid")
    args = ["connect", ssid, password or "", iface, "yes" if hidden else "no"]
    rc, out, err = _run_script("wifi_profile.sh", args, timeout=35, use_sudo=True)
    if rc != 0:
        raise NetControlError(code="wifi_connect_failed", message="Failed to connect Wi-Fi", detail=err or out)
    return {"ssid": ssid, "ifname": iface, "hidden": bool(hidden), "stdout": out}


def wifi_profiles_list() -> dict:
    rc, out, err = _run_script("wifi_profile.sh", ["profiles"], timeout=12, use_sudo=True)
    if rc != 0:
        raise NetControlError(code="wifi_profiles_failed", message="Failed to list Wi-Fi profiles", detail=err or out)
    return {"profiles": _parse_wifi_profiles_output(out), "stdout": out}


def wifi_profile_set(ssid: str, priority: int, autoconnect: bool) -> dict:
    ssid = (ssid or "").strip()
    if not ssid:
        raise NetControlError(code="invalid_payload", message="Missing ssid")
    prio = int(priority)
    auto = "yes" if autoconnect else "no"
    rc, out, err = _run_script("wifi_profile.sh", ["profile-set", ssid, str(prio), auto], timeout=12, use_sudo=True)
    if rc != 0:
        raise NetControlError(code="wifi_profile_set_failed", message="Failed to set Wi-Fi profile", detail=err or out)
    return {"ssid": ssid, "priority": prio, "autoconnect": autoconnect, "stdout": out}


def wifi_profile_delete(ssid: str, uuid: str = "") -> dict:
    ssid = (ssid or "").strip()
    uuid = (uuid or "").strip()
    if not ssid:
        raise NetControlError(code="invalid_payload", message="Missing ssid")
    rc, out, err = _run_script("wifi_profile.sh", ["profile-delete", ssid, uuid], timeout=12, use_sudo=True)
    if rc != 0:
        raise NetControlError(code="wifi_profile_delete_failed", message="Failed to delete Wi-Fi profile", detail=err or out)
    return {"ssid": ssid, "uuid": uuid, "stdout": out}


def wifi_profile_up(ssid: str, uuid: str = "") -> dict:
    ssid = (ssid or "").strip()
    uuid = (uuid or "").strip()
    if not ssid:
        raise NetControlError(code="invalid_payload", message="Missing ssid")
    rc, out, err = _run_script("wifi_profile.sh", ["profile-up", ssid, uuid], timeout=25, use_sudo=True)
    if rc != 0:
        detail = err or out
        if _is_missing_wifi_secret_error(detail):
            raise NetControlError(
                code="wifi_secrets_required",
                message="Stored Wi-Fi profile requires credentials or WPS pairing",
                detail=detail,
            )
        raise NetControlError(code="wifi_profile_up_failed", message="Failed to activate Wi-Fi profile", detail=detail)
    return {"ssid": ssid, "stdout": out}


def wifi_disconnect(ifname: str = DEFAULT_WIFI_IFACE) -> dict:
    iface = (ifname or DEFAULT_WIFI_IFACE).strip() or DEFAULT_WIFI_IFACE
    rc, out, err = _run_script("wifi_disconnect.sh", [iface], timeout=12, use_sudo=True)
    parsed = _parse_kv_output(out)
    if rc != 0 and rc != 10:
        raise NetControlError(code="wifi_disconnect_failed", message="Failed to disconnect Wi-Fi", detail=err or out)
    return {"ifname": iface, "stdout": out, "rc": parsed.get("rc", str(rc))}


def wifi_request_dhcp(ifname: str = DEFAULT_WIFI_IFACE) -> dict:
    iface = (ifname or DEFAULT_WIFI_IFACE).strip() or DEFAULT_WIFI_IFACE
    rc, out, err = _run_script("wifi_dhcp.sh", [iface], timeout=35, use_sudo=True)
    if rc != 0:
        raise NetControlError(code="wifi_dhcp_failed", message="Failed to request DHCP lease", detail=err or out)
    parsed = _parse_kv_output(out)
    return {"ifname": iface, "ip": parsed.get("ip", ""), "stdout": out}


def get_wifi_status(ifname: str = DEFAULT_WIFI_IFACE) -> dict:
    iface = (ifname or DEFAULT_WIFI_IFACE).strip() or DEFAULT_WIFI_IFACE
    rc, out, err = _run_script("wifi_status.sh", [iface], timeout=8, use_sudo=True)
    if rc != 0:
        raise NetControlError(code="wifi_status_failed", message="Failed to read Wi-Fi status", detail=err or out)
    parsed = _parse_kv_output(out)
    return {
        "ifname": parsed.get("iface", iface),
        "radio": parsed.get("radio", "unknown"),
        "device_state": parsed.get("device_state", ""),
        "connection": parsed.get("connection", ""),
        "connected": parsed.get("connected", "false").lower() == "true",
        "wpa_state": parsed.get("wpa_state", ""),
        "ssid": parsed.get("ssid", ""),
        "bssid": parsed.get("bssid", ""),
        "signal": parsed.get("signal", ""),
        "frequency_mhz": parsed.get("frequency_mhz", ""),
        "security": parsed.get("security", ""),
        "ip": parsed.get("ip", ""),
    }


def set_ap_enabled(enabled: bool, ifname: str = DEFAULT_WIFI_IFACE, profile: str = DEFAULT_AP_PROFILE) -> dict:
    iface = (ifname or DEFAULT_WIFI_IFACE).strip() or DEFAULT_WIFI_IFACE
    ap_profile = (profile or DEFAULT_AP_PROFILE).strip() or DEFAULT_AP_PROFILE
    if iface not in ALLOWED_WIFI_INTERFACES:
        raise NetControlError(code="invalid_interface", message=f"Interface {iface!r} is not allowed")
    script_name = "ap_enable.sh" if enabled else "ap_disable.sh"
    rc, out, err = _run_script(script_name, [iface, ap_profile], timeout=20, use_sudo=True)
    if rc != 0:
        raise NetControlError(code="ap_toggle_failed", message="Failed to toggle AP hotspot", detail=err or out)
    parsed = _parse_kv_output(out)
    return {
        "ifname": parsed.get("ifname", iface),
        "profile": parsed.get("profile", ap_profile),
        "enabled": parsed.get("enabled", "false").lower() == "true",
        "stdout": out,
    }


def get_ap_status(ifname: str = DEFAULT_WIFI_IFACE, profile: str = DEFAULT_AP_PROFILE) -> dict:
    iface = (ifname or DEFAULT_WIFI_IFACE).strip() or DEFAULT_WIFI_IFACE
    ap_profile = (profile or DEFAULT_AP_PROFILE).strip() or DEFAULT_AP_PROFILE
    if iface not in ALLOWED_WIFI_INTERFACES:
        raise NetControlError(code="invalid_interface", message=f"Interface {iface!r} is not allowed")
    rc, out, err = _run_script("ap_status.sh", [iface, ap_profile], timeout=8, use_sudo=True)
    if rc != 0:
        raise NetControlError(code="ap_status_failed", message="Failed to read AP status", detail=err or out)
    parsed = _parse_kv_output(out)
    try:
        clients_count = int(parsed.get("clients_count", "0"))
    except Exception:
        clients_count = 0
    return {
        "ifname": parsed.get("ifname", iface),
        "profile": parsed.get("profile", ap_profile),
        "active": parsed.get("active", "false").lower() == "true",
        "ssid": parsed.get("ssid", ""),
        "ip": parsed.get("ip", ""),
        "portal_url": parsed.get("portal_url", ""),
        "clients_count": clients_count,
        "radio": parsed.get("radio", ""),
        "device_state": parsed.get("device_state", ""),
        "active_connection": parsed.get("active_connection", ""),
    }


def get_ap_clients(ifname: str = DEFAULT_WIFI_IFACE) -> dict:
    iface = (ifname or DEFAULT_WIFI_IFACE).strip() or DEFAULT_WIFI_IFACE
    if iface not in ALLOWED_WIFI_INTERFACES:
        raise NetControlError(code="invalid_interface", message=f"Interface {iface!r} is not allowed")
    rc, out, err = _run_script("ap_clients.sh", [iface], timeout=12, use_sudo=True)
    if rc != 0:
        raise NetControlError(code="ap_clients_failed", message="Failed to read AP clients", detail=err or out)
    try:
        payload = json.loads(out) if out else {}
    except Exception as exc:
        raise NetControlError(code="ap_clients_invalid_json", message="AP clients script returned invalid JSON", detail=str(exc))
    if not isinstance(payload, dict):
        payload = {}
    clients = payload.get("clients")
    if not isinstance(clients, list):
        clients = []
    return {"ifname": iface, "clients": clients}


def storage_probe() -> dict:
    rc, out, err = _run_script("storage_probe.sh", [], timeout=12, use_sudo=False)
    if rc != 0:
        raise NetControlError(code="storage_probe_failed", message="Failed to read storage devices", detail=err or out)
    try:
        payload = json.loads(out) if out else {}
    except Exception as exc:
        raise NetControlError(code="storage_probe_invalid_json", message="Storage probe returned invalid JSON", detail=str(exc))
    if not isinstance(payload, dict):
        return {"devices": []}
    devices = payload.get("devices")
    if not isinstance(devices, list):
        devices = []
    return {"detected_at": payload.get("detected_at", ""), "devices": devices}


def storage_mount(selector_type: str, selector_value: str, mount_path: str, mount_options: str = "defaults,noatime,nofail") -> dict:
    sel_type = (selector_type or "").strip().lower()
    sel_val = (selector_value or "").strip()
    mnt_path = (mount_path or "").strip()
    opts = (mount_options or "defaults,noatime,nofail").strip()
    if sel_type not in ("uuid", "partuuid"):
        raise NetControlError(code="invalid_selector_type", message="Storage mount selector must be uuid or partuuid")
    if not sel_val:
        raise NetControlError(code="invalid_selector_value", message="Storage mount selector is required")
    if not mnt_path:
        raise NetControlError(code="invalid_mount_path", message="Storage mount path is required")
    rc, out, err = _run_script("storage_mount.sh", [sel_type, sel_val, mnt_path, opts], timeout=20, use_sudo=True)
    if rc != 0:
        raise NetControlError(code="storage_mount_failed", message="Failed to mount storage device", detail=err or out)
    parsed = _parse_kv_output(out)
    return {
        "mounted": parsed.get("mounted", "false").lower() == "true",
        "mount_path": parsed.get("mount_path", mnt_path),
        "device": parsed.get("device", ""),
    }


def storage_internal_mount() -> dict:
    rc, out, err = _run_script("storage_internal_mount.sh", [], timeout=20, use_sudo=True)
    if rc != 0:
        raise NetControlError(code="storage_internal_mount_failed", message="Failed to mount internal media loop", detail=err or out)
    parsed = _parse_kv_output(out)
    return {
        "mounted": parsed.get("mounted", "false").lower() == "true",
        "mount_path": parsed.get("mount_path", "/mnt/deviceportal/media"),
        "device": parsed.get("device", ""),
        "filesystem": parsed.get("filesystem", ""),
    }


def storage_format(selector_type: str, selector_value: str, filesystem: str = "vfat", label: str = "") -> dict:
    sel_type = (selector_type or "").strip().lower()
    sel_val = (selector_value or "").strip()
    fs_type = (filesystem or "vfat").strip().lower()
    fs_label = (label or "").strip()
    if sel_type not in ("uuid", "partuuid"):
        raise NetControlError(code="invalid_selector_type", message="Storage format selector must be uuid or partuuid")
    if not sel_val:
        raise NetControlError(code="invalid_selector_value", message="Storage format selector is required")
    if fs_type not in ("ext4", "vfat", "exfat"):
        raise NetControlError(code="invalid_filesystem", message="Unsupported filesystem")
    rc, out, err = _run_script("storage_format.sh", [sel_type, sel_val, fs_type, fs_label], timeout=120, use_sudo=True)
    if rc != 0:
        raise NetControlError(code="storage_format_failed", message="Failed to format storage device", detail=err or out)
    parsed = _parse_kv_output(out)
    return {
        "formatted": parsed.get("formatted", "false").lower() == "true",
        "device": parsed.get("device", ""),
        "filesystem": parsed.get("filesystem", fs_type),
        "label": parsed.get("label", fs_label),
        "uuid": parsed.get("uuid", ""),
        "part_uuid": parsed.get("partuuid", ""),
    }


def storage_unmount(mount_path: str) -> dict:
    mnt_path = (mount_path or "").strip()
    if not mnt_path:
        raise NetControlError(code="invalid_mount_path", message="Storage mount path is required")
    rc, out, err = _run_script("storage_unmount.sh", [mnt_path], timeout=20, use_sudo=True)
    if rc != 0:
        raise NetControlError(code="storage_unmount_failed", message="Failed to unmount storage device", detail=err or out)
    parsed = _parse_kv_output(out)
    return {
        "mounted": parsed.get("mounted", "false").lower() == "true",
        "mount_path": parsed.get("mount_path", mnt_path),
    }


def start_wps(ifname: str = DEFAULT_WIFI_IFACE, target_bssid: str = "", target_ssid: str = "") -> dict:
    iface = (ifname or DEFAULT_WIFI_IFACE).strip() or DEFAULT_WIFI_IFACE
    if iface not in ALLOWED_WIFI_INTERFACES:
        raise NetControlError(code="invalid_interface", message=f"Interface {iface!r} is not allowed")
    bssid = (target_bssid or "").strip()
    ssid = (target_ssid or "").strip()
    # Avoid blocking HTTP for the full WPS window (typically 120s),
    # because reverse proxies often time out earlier (e.g. 60s -> 504).
    # The UI already polls network state after triggering WPS.
    rc, out, err = _run_script("wps_start.sh", [iface, "0", bssid, ssid], timeout=25, use_sudo=True)
    parsed = _parse_kv_output(out)
    detail = parsed.get("details") or err or out
    if rc != 0:
        raise NetControlError(
            code=parsed.get("code", "wps_failed"),
            message=parsed.get("message", "Failed to start WPS"),
            detail=detail,
        )
    return {
        "ifname": parsed.get("iface", iface),
        "success": parsed.get("success", "true").lower() == "true",
        "code": parsed.get("code", "ok"),
        "message": parsed.get(
            "message",
            "WPS wurde gestartet. Bitte jetzt innerhalb von 2 Minuten am Router die WPS-Taste druecken.",
        ),
        "details": detail,
        "hint": parsed.get("hint", "Je nach Router kann die Verbindung 30-120 Sekunden dauern."),
        "network": {
            "ssid": parsed.get("ssid", ""),
            "connection": parsed.get("connection", ""),
            "bssid": parsed.get("bssid", ""),
            "signal": parsed.get("signal", ""),
            "frequency_mhz": parsed.get("frequency_mhz", ""),
            "security": parsed.get("security", ""),
            "ip": parsed.get("ip", ""),
        },
    }


def disable_tailscale_dns_override() -> dict:
    rc, out, err = _run_script("tailscale_dns_fix.sh", [], timeout=30, use_sudo=True)
    parsed = _parse_kv_output(out)
    detail = parsed.get("details") or err or out
    if rc != 0:
        raise NetControlError(
            code=parsed.get("code", "tailscale_dns_fix_failed"),
            message=parsed.get("message", "Failed to disable Tailscale DNS takeover"),
            detail=detail,
        )
    return {
        "success": parsed.get("success", "true").lower() == "true",
        "code": parsed.get("code", "ok"),
        "message": parsed.get("message", "Tailscale DNS takeover disabled"),
        "connection": parsed.get("connection", ""),
        "dns": parsed.get("dns", ""),
        "search": parsed.get("search", ""),
    }


def portal_update(service_name: str = "device-portal.service") -> dict:
    repo_dir = str(REPO_ROOT.resolve())
    service_name = (service_name or "device-portal.service").strip() or "device-portal.service"
    service_user = _service_user_from_systemd(service_name) or getpass.getuser()
    update_dir = str(_update_dir())
    rc, out, err = _run_script(
        "portal_update.sh",
        ["start", repo_dir, service_user, service_name, update_dir],
        timeout=25,
        use_sudo=True,
    )
    parsed = _parse_kv_output(out)
    detail = parsed.get("details") or err or out
    if rc != 0:
        raise NetControlError(
            code=parsed.get("code", "portal_update_failed"),
            message=parsed.get("message", "Portal update failed"),
            detail=detail,
        )
    return {
        "success": parsed.get("success", "true").lower() == "true",
        "repo_dir": parsed.get("repo_dir", repo_dir),
        "service_user": parsed.get("service_user", service_user),
        "service_name": parsed.get("service_name", service_name),
        "git_status": parsed.get("git_status", "unknown"),
        "restart_scheduled": parsed.get("restart_scheduled", "false").lower() == "true",
        "job_id": parsed.get("job_id", ""),
        "status": "running",
        "started_at": parsed.get("started_at", ""),
        "message": parsed.get("message", "Portal update started."),
        "details": detail,
    }


def portal_update_status(job_id: str = "", max_log_bytes: int = MAX_UPDATE_LOG_BYTES) -> dict:
    updates_dir = _update_dir()
    selected_job_id = (job_id or "").strip()
    if selected_job_id:
        if not _is_valid_job_id(selected_job_id):
            raise NetControlError(code="invalid_job_id", message="Invalid update job id")
    else:
        state_files = sorted(updates_dir.glob("*.state"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not state_files:
            return {
                "job_id": "",
                "status": "idle",
                "success": False,
                "message": "No update run found yet.",
                "log": "",
            }
        selected_job_id = state_files[0].stem

    state_file = updates_dir / f"{selected_job_id}.state"
    log_file = updates_dir / f"{selected_job_id}.log"
    if not state_file.exists():
        raise NetControlError(code="job_not_found", message="Update job not found")

    try:
        raw_state = state_file.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        raise NetControlError(code="update_state_read_failed", message="Failed to read update state", detail=str(exc))
    parsed = _parse_kv_output(raw_state)
    status = (parsed.get("status") or "unknown").strip().lower()
    success = (parsed.get("success") or "false").strip().lower() == "true"
    if status == "done":
        message = "Portal update completed."
    elif status == "failed":
        message = "Portal update failed."
    elif status == "restarting":
        message = "Service restart in progress."
    elif status == "running":
        message = "Portal update running."
    else:
        message = f"Portal update status: {status or 'unknown'}"

    return {
        "job_id": selected_job_id,
        "status": status,
        "success": success,
        "repo_dir": parsed.get("repo_dir", ""),
        "service_user": parsed.get("service_user", ""),
        "service_name": parsed.get("service_name", ""),
        "git_status": parsed.get("git_status", "unknown"),
        "before_commit": parsed.get("before_commit", ""),
        "after_commit": parsed.get("after_commit", ""),
        "started_at": parsed.get("started_at", ""),
        "updated_at": parsed.get("updated_at", ""),
        "finished_at": parsed.get("finished_at", ""),
        "message": message,
        "log": _tail_file(log_file, max_bytes=max_log_bytes) if log_file.exists() else "",
        "log_file": str(log_file),
    }
