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


def start_wps(ifname: str = DEFAULT_WIFI_IFACE) -> dict:
    iface = (ifname or DEFAULT_WIFI_IFACE).strip() or DEFAULT_WIFI_IFACE
    if iface not in ALLOWED_WIFI_INTERFACES:
        raise NetControlError(code="invalid_interface", message=f"Interface {iface!r} is not allowed")
    # Avoid blocking HTTP for the full WPS window (typically 120s),
    # because reverse proxies often time out earlier (e.g. 60s -> 504).
    # The UI already polls network state after triggering WPS.
    rc, out, err = _run_script("wps_start.sh", [iface, "0"], timeout=25, use_sudo=True)
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
