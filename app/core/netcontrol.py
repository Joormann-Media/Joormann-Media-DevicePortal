from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


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
PREFERRED_WIFI_PRIORITY = 999
NONPREFERRED_WIFI_PRIORITY = 100


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


def wifi_connect(ssid: str, password: str = "", ifname: str = DEFAULT_WIFI_IFACE) -> dict:
    ssid = (ssid or "").strip()
    iface = (ifname or DEFAULT_WIFI_IFACE).strip() or DEFAULT_WIFI_IFACE
    if not ssid:
        raise NetControlError(code="invalid_payload", message="Missing ssid")
    args = ["connect", ssid, password or "", iface]
    rc, out, err = _run_script("wifi_profile.sh", args, timeout=35, use_sudo=True)
    if rc != 0:
        raise NetControlError(code="wifi_connect_failed", message="Failed to connect Wi-Fi", detail=err or out)
    return {"ssid": ssid, "ifname": iface, "stdout": out}


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


def wifi_profile_delete(ssid: str) -> dict:
    ssid = (ssid or "").strip()
    if not ssid:
        raise NetControlError(code="invalid_payload", message="Missing ssid")
    rc, out, err = _run_script("wifi_profile.sh", ["profile-delete", ssid], timeout=12, use_sudo=True)
    if rc != 0:
        raise NetControlError(code="wifi_profile_delete_failed", message="Failed to delete Wi-Fi profile", detail=err or out)
    return {"ssid": ssid, "stdout": out}


def wifi_profile_up(ssid: str) -> dict:
    ssid = (ssid or "").strip()
    if not ssid:
        raise NetControlError(code="invalid_payload", message="Missing ssid")
    rc, out, err = _run_script("wifi_profile.sh", ["profile-up", ssid], timeout=25, use_sudo=True)
    if rc != 0:
        raise NetControlError(code="wifi_profile_up_failed", message="Failed to activate Wi-Fi profile", detail=err or out)
    return {"ssid": ssid, "stdout": out}


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
