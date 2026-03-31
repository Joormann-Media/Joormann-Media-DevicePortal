from __future__ import annotations

import json
import os
import subprocess
import getpass
import re
import shutil
import pwd
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


def _player_update_dir() -> Path:
    path = Path(ASSET_DIR) / "updates-player"
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


def _run_script(script_name: str, args: list[str], timeout: int, use_sudo: bool, env: dict | None = None) -> tuple[int, str, str]:
    script_path = _resolve_script(script_name)
    cmd: list[str] = [script_path] + args
    if use_sudo:
        cmd = ["sudo", "-n"] + cmd
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except FileNotFoundError as exc:
        raise NetControlError(code="command_not_found", message="Required runtime command missing", detail=str(exc))
    except subprocess.TimeoutExpired:
        raise NetControlError(code="timeout", message=f"Command timed out after {timeout}s")
    except Exception as exc:
        raise NetControlError(code="execution_failed", message="Could not execute netcontrol command", detail=str(exc))
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def _runtime_env_for_user(user: str | None = None) -> dict | None:
    try:
        if user:
            uid = pwd.getpwnam(user).pw_uid
        else:
            uid = os.getuid()
    except Exception:
        uid = os.getuid()
    runtime_dir = f"/run/user/{uid}"
    if not os.path.isdir(runtime_dir):
        return None
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", runtime_dir)
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime_dir}/bus")
    return env


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


def _parse_json_output(raw: str, *, code: str, message: str) -> dict:
    try:
        payload = json.loads(raw) if raw else {}
    except Exception as exc:
        raise NetControlError(code=code, message=message, detail=f"invalid json: {exc}")
    if not isinstance(payload, dict):
        raise NetControlError(code=code, message=message, detail="payload must be JSON object")
    return payload


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


def _sanitize_hostname(value: str) -> str:
    raw = (value or "").strip().lower()
    raw = re.sub(r"[\s_]+", "-", raw)
    raw = re.sub(r"[^a-z0-9-]", "-", raw)
    raw = re.sub(r"-{2,}", "-", raw).strip("-")
    if not raw:
        raise NetControlError(code="invalid_hostname", message="Hostname must contain letters or numbers")
    if len(raw) > 63:
        raw = raw[:63].rstrip("-")
    if not raw:
        raise NetControlError(code="invalid_hostname", message="Hostname is invalid after sanitizing")
    return raw


def _allowed_lan_interfaces() -> set[str]:
    interfaces: set[str] = set()
    try:
        for path in Path("/sys/class/net").iterdir():
            ifname = path.name
            if ifname == "lo":
                continue
            if (path / "wireless").exists():
                continue
            if ifname.startswith("eth") or ifname.startswith("en"):
                interfaces.add(ifname)
    except Exception:
        pass
    if not interfaces:
        interfaces = set(ALLOWED_LAN_INTERFACES)
    return interfaces


def _derive_ap_ssid(hostname: str) -> str:
    return f"{hostname}-ap"[:32]


def _derive_bt_name(hostname: str) -> str:
    return f"{hostname}-bt"[:64]


def get_network_info() -> dict:
    rc, out, err = _run_script("network_info.sh", [], timeout=12, use_sudo=False)
    if rc != 0:
        raise NetControlError(code="network_info_failed", message="Failed to read network status", detail=err or out)
    try:
        payload = json.loads(out) if out else {}
    except Exception as exc:
        raise NetControlError(code="network_info_invalid_json", message="Network status script returned invalid JSON", detail=str(exc))
    if not isinstance(payload, dict):
        raise NetControlError(code="network_info_invalid", message="Network status payload must be a JSON object")
    return payload


def hostname_rename_preview(new_hostname: str, ap_profile: str = DEFAULT_AP_PROFILE) -> dict:
    next_hostname = _sanitize_hostname(new_hostname)
    current = get_network_info()
    current_hostname = str(current.get("hostname") or "").strip()
    if not current_hostname:
        current_hostname = os.uname().nodename.strip()

    ap_info: dict = {}
    try:
        ap_info = get_ap_status(ifname=DEFAULT_WIFI_IFACE, profile=(ap_profile or DEFAULT_AP_PROFILE))
    except Exception:
        ap_info = {}

    interfaces = current.get("interfaces") if isinstance(current.get("interfaces"), dict) else {}
    wifi = interfaces.get("wifi") if isinstance(interfaces.get("wifi"), dict) else {}
    lan = interfaces.get("lan") if isinstance(interfaces.get("lan"), dict) else {}
    bt = interfaces.get("bluetooth") if isinstance(interfaces.get("bluetooth"), dict) else {}

    return {
        "current_hostname": current_hostname or "-",
        "next_hostname": next_hostname,
        "derived": {
            "ap_ssid": _derive_ap_ssid(next_hostname),
            "bt_name": _derive_bt_name(next_hostname),
        },
        "connections": {
            "lan": {
                "ifname": lan.get("ifname") or "eth0",
                "ip": lan.get("ip") or "",
                "carrier": bool(lan.get("carrier")),
            },
            "wifi": {
                "ifname": wifi.get("ifname") or DEFAULT_WIFI_IFACE,
                "connected": bool(wifi.get("connected")),
                "ssid": wifi.get("ssid") or "",
                "ip": wifi.get("ip") or "",
            },
            "bluetooth": {
                "enabled": bool(bt.get("enabled")),
            },
            "ap": {
                "profile": ap_info.get("profile") or (ap_profile or DEFAULT_AP_PROFILE),
                "active": bool(ap_info.get("active")),
                "ssid": ap_info.get("ssid") or "",
                "ip": ap_info.get("ip") or "",
            },
        },
    }


def apply_hostname_rename(new_hostname: str, ap_profile: str = DEFAULT_AP_PROFILE) -> dict:
    safe_hostname = _sanitize_hostname(new_hostname)
    profile = (ap_profile or DEFAULT_AP_PROFILE).strip() or DEFAULT_AP_PROFILE
    rc, out, err = _run_script("hostname_rename.sh", [safe_hostname, profile], timeout=35, use_sudo=True)
    parsed = _parse_kv_output(out)
    detail = parsed.get("details") or err or out
    if rc != 0:
        raise NetControlError(
            code=parsed.get("code", "hostname_rename_failed"),
            message=parsed.get("message", "Hostname rename failed"),
            detail=detail,
        )
    return {
        "success": parsed.get("success", "true").lower() == "true",
        "old_hostname": parsed.get("old_hostname", ""),
        "new_hostname": parsed.get("new_hostname", safe_hostname),
        "ap_profile": parsed.get("ap_profile", profile),
        "ap_ssid": parsed.get("ap_ssid", _derive_ap_ssid(safe_hostname)),
        "bt_name": parsed.get("bt_name", _derive_bt_name(safe_hostname)),
        "message": parsed.get("message", "Hostname updated"),
        "details": detail,
        "requires_reconnect": parsed.get("requires_reconnect", "true").lower() == "true",
    }


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


def _parse_bool_flag(value: str | None) -> bool | None:
    raw = (value or "").strip().lower()
    if raw in ("1", "true", "yes", "on", "enabled"):
        return True
    if raw in ("0", "false", "no", "off", "disabled"):
        return False
    return None


def _parse_int_flag(value: str | None) -> int | None:
    raw = (value or "").strip()
    if raw == "":
        return None
    try:
        return int(raw, 0)
    except Exception:
        return None


def get_bluetooth_status() -> dict:
    rc, out, err = _run_script("bluetooth_ctl.sh", ["status"], timeout=10, use_sudo=True)
    if rc != 0:
        raise NetControlError(code="bluetooth_status_failed", message="Failed to read Bluetooth status", detail=err or out)
    parsed = _parse_kv_output(out)
    return {
        "enabled": _parse_bool_flag(parsed.get("powered")),
        "discoverable": _parse_bool_flag(parsed.get("discoverable")),
        "pairable": _parse_bool_flag(parsed.get("pairable")),
        "discoverable_timeout": _parse_int_flag(parsed.get("discoverable_timeout")),
        "pairable_timeout": _parse_int_flag(parsed.get("pairable_timeout")),
        "stdout": out,
    }


def set_bluetooth_runtime_settings(
    *,
    discoverable: bool | None = None,
    discoverable_timeout: int | None = None,
    pairable: bool | None = None,
    pairable_timeout: int | None = None,
) -> dict:
    if discoverable_timeout is not None and (discoverable_timeout < 0 or discoverable_timeout > 86400):
        raise NetControlError(code="invalid_payload", message="discoverable_timeout must be between 0 and 86400 seconds")
    if pairable_timeout is not None and (pairable_timeout < 0 or pairable_timeout > 86400):
        raise NetControlError(code="invalid_payload", message="pairable_timeout must be between 0 and 86400 seconds")

    discoverable_arg = "keep" if discoverable is None else ("on" if discoverable else "off")
    discoverable_timeout_arg = "keep" if discoverable_timeout is None else str(int(discoverable_timeout))
    pairable_arg = "keep" if pairable is None else ("on" if pairable else "off")
    pairable_timeout_arg = "keep" if pairable_timeout is None else str(int(pairable_timeout))

    rc, out, err = _run_script(
        "bluetooth_ctl.sh",
        ["config", discoverable_arg, discoverable_timeout_arg, pairable_arg, pairable_timeout_arg],
        timeout=12,
        use_sudo=True,
    )
    if rc != 0:
        raise NetControlError(code="bluetooth_config_failed", message="Failed to configure Bluetooth runtime settings", detail=err or out)

    parsed = _parse_kv_output(out)
    return {
        "enabled": _parse_bool_flag(parsed.get("powered")),
        "discoverable": _parse_bool_flag(parsed.get("discoverable")),
        "pairable": _parse_bool_flag(parsed.get("pairable")),
        "discoverable_timeout": _parse_int_flag(parsed.get("discoverable_timeout")),
        "pairable_timeout": _parse_int_flag(parsed.get("pairable_timeout")),
        "stdout": out,
    }


def get_bluetooth_pairing_feedback(window_seconds: int = 300) -> dict:
    safe_window = max(30, min(3600, int(window_seconds or 300)))
    rc, out, err = _run_script("bluetooth_pairing_feedback.sh", [str(safe_window)], timeout=12, use_sudo=True)
    if rc != 0:
        raise NetControlError(
            code="bluetooth_pairing_feedback_failed",
            message="Failed to read Bluetooth pairing feedback",
            detail=err or out,
        )
    parsed = _parse_kv_output(out)
    return {
        "passkey": (parsed.get("passkey") or "").strip(),
        "pending_mac": (parsed.get("pending_mac") or "").strip(),
        "device_mac": (parsed.get("device_mac") or "").strip(),
        "device_name": (parsed.get("device_name") or "").strip(),
        "passkey_line": (parsed.get("passkey_line") or "").strip(),
        "recent_line": (parsed.get("recent_line") or "").strip(),
    }


def start_bluetooth_pairing_session(timeout_seconds: int = 180) -> dict:
    safe_timeout = max(30, min(900, int(timeout_seconds or 180)))
    rc, out, err = _run_script("bluetooth_pairing_session.sh", ["start", str(safe_timeout)], timeout=20, use_sudo=True)
    if rc != 0:
        raise NetControlError(
            code="bluetooth_pairing_start_failed",
            message="Failed to start Bluetooth pairing session",
            detail=err or out,
        )
    parsed = _parse_kv_output(out)
    return {
        "active": _parse_bool_flag(parsed.get("active")) is True,
        "pid": _parse_int_flag(parsed.get("pid")),
        "timeout_seconds": _parse_int_flag(parsed.get("timeout")) or safe_timeout,
        "stdout": out,
    }


def stop_bluetooth_pairing_session() -> dict:
    rc, out, err = _run_script("bluetooth_pairing_session.sh", ["stop"], timeout=20, use_sudo=True)
    if rc != 0:
        raise NetControlError(
            code="bluetooth_pairing_stop_failed",
            message="Failed to stop Bluetooth pairing session",
            detail=err or out,
        )
    parsed = _parse_kv_output(out)
    return {
        "active": _parse_bool_flag(parsed.get("active")) is True,
        "stdout": out,
    }


def get_bluetooth_pairing_session_status() -> dict:
    rc, out, err = _run_script("bluetooth_pairing_session.sh", ["status"], timeout=8, use_sudo=True)
    if rc != 0:
        raise NetControlError(
            code="bluetooth_pairing_status_failed",
            message="Failed to read Bluetooth pairing session status",
            detail=err or out,
        )
    parsed = _parse_kv_output(out)
    return {
        "active": _parse_bool_flag(parsed.get("active")) is True,
        "pid": _parse_int_flag(parsed.get("pid")),
        "stdout": out,
    }


def bluetooth_pairing_action(action: str, target_mac: str) -> dict:
    normalized_action = (action or "").strip().lower()
    if normalized_action not in ("confirm", "reject"):
        raise NetControlError(code="invalid_payload", message="action must be confirm or reject")

    mac = (target_mac or "").strip()
    if not re.fullmatch(r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", mac):
        raise NetControlError(code="invalid_payload", message="target_mac must be a valid MAC address")

    rc, out, err = _run_script("bluetooth_pairing_action.sh", [normalized_action, mac], timeout=20, use_sudo=True)
    if rc != 0:
        raise NetControlError(
            code="bluetooth_pairing_action_failed",
            message="Failed to apply Bluetooth pairing action",
            detail=err or out,
        )

    parsed = _parse_kv_output(out)
    return {
        "action": (parsed.get("action") or normalized_action).strip(),
        "mac": (parsed.get("mac") or mac).strip(),
        "name": (parsed.get("name") or "").strip(),
        "paired": _parse_bool_flag(parsed.get("paired")),
        "trusted": _parse_bool_flag(parsed.get("trusted")),
        "connected": _parse_bool_flag(parsed.get("connected")),
        "stdout_tail": (parsed.get("stdout_tail") or "").strip(),
        "stderr_tail": (parsed.get("stderr_tail") or "").strip(),
        "stdout": out,
    }


def get_bluetooth_paired_devices() -> list[dict]:
    rc, out, err = _run_script("bluetooth_paired_devices.sh", [], timeout=12, use_sudo=True)
    if rc != 0:
        raise NetControlError(
            code="bluetooth_paired_devices_failed",
            message="Failed to read paired Bluetooth devices",
            detail=err or out,
        )
    devices: list[dict] = []
    seen: set[str] = set()
    for line in (out or "").splitlines():
        line = line.strip()
        if not line.startswith("device="):
            continue
        payload = line.split("=", 1)[1]
        mac, _, name = payload.partition("|")
        mac = mac.strip().upper()
        name = name.strip()
        if not mac or mac in seen:
            continue
        seen.add(mac)
        devices.append({"mac": mac, "name": name})
    return devices


def bluetooth_audio_scan(scan_seconds: int = 8) -> dict:
    seconds = max(4, min(30, int(scan_seconds or 8)))
    # Scan+device-info can take noticeably longer on busy airspace.
    env = dict(os.environ)
    env["BTCTL_HARD_TIMEOUT"] = str(max(12, min(45, seconds + 10)))
    rc, out, err = _run_script("bluetooth_audio.py", ["scan", str(seconds)], timeout=max(20, seconds + 20), use_sudo=False, env=env)
    if rc != 0:
        raise NetControlError(code="bluetooth_scan_failed", message="Failed to scan Bluetooth devices", detail=err or out)
    payload = _parse_json_output(out, code="bluetooth_scan_invalid_json", message="Bluetooth scan returned invalid JSON")
    if not payload.get("ok", False):
        raise NetControlError(code="bluetooth_scan_failed", message="Failed to scan Bluetooth devices", detail=str(payload.get("error") or err or out))
    return payload


def bluetooth_audio_devices() -> dict:
    env = dict(os.environ)
    env["BTCTL_HARD_TIMEOUT"] = "12"
    rc, out, err = _run_script("bluetooth_audio.py", ["devices"], timeout=18, use_sudo=False, env=env)
    if rc != 0:
        raise NetControlError(code="bluetooth_devices_failed", message="Failed to list Bluetooth devices", detail=err or out)
    payload = _parse_json_output(out, code="bluetooth_devices_invalid_json", message="Bluetooth devices returned invalid JSON")
    if not payload.get("ok", False):
        raise NetControlError(code="bluetooth_devices_failed", message="Failed to list Bluetooth devices", detail=str(payload.get("error") or err or out))
    return payload


def bluetooth_audio_action(action: str, device_id: str) -> dict:
    normalized = (action or "").strip().lower()
    if normalized not in ("pair", "connect", "disconnect", "forget"):
        raise NetControlError(code="invalid_payload", message="action must be pair|connect|disconnect|forget")
    mac = (device_id or "").strip().upper()
    if not re.fullmatch(r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", mac):
        raise NetControlError(code="invalid_payload", message="device_id must be a valid MAC address")
    env = dict(os.environ)
    env["BTCTL_HARD_TIMEOUT"] = "40"
    rc, out, err = _run_script("bluetooth_audio.py", [normalized, mac], timeout=50, use_sudo=False, env=env)
    if rc != 0:
        raise NetControlError(code="bluetooth_action_failed", message=f"Failed to {normalized} bluetooth device", detail=err or out)
    payload = _parse_json_output(out, code="bluetooth_action_invalid_json", message="Bluetooth action returned invalid JSON")
    if not payload.get("ok", False):
        raise NetControlError(
            code="bluetooth_action_failed",
            message=f"Failed to {normalized} bluetooth device",
            detail=str(payload.get("error") or err or out),
        )
    return payload


def audio_outputs_status() -> dict:
    env = _runtime_env_for_user(getpass.getuser())
    rc, out, err = _run_script("audio_output_ctl.py", ["status"], timeout=20, use_sudo=False, env=env)
    if rc != 0:
        raise NetControlError(code="audio_outputs_failed", message="Failed to read audio outputs", detail=err or out)
    payload = _parse_json_output(out, code="audio_outputs_invalid_json", message="Audio outputs returned invalid JSON")
    if not payload.get("ok", False):
        raise NetControlError(code="audio_outputs_failed", message="Failed to read audio outputs", detail=str(payload.get("error") or err or out))
    return payload


def audio_output_set(output_id: str) -> dict:
    target = (output_id or "").strip()
    if not target:
        raise NetControlError(code="invalid_payload", message="output is required")
    env = _runtime_env_for_user(getpass.getuser())
    rc, out, err = _run_script("audio_output_ctl.py", ["set", target], timeout=25, use_sudo=False, env=env)
    if rc != 0:
        raise NetControlError(code="audio_output_set_failed", message="Failed to set audio output", detail=err or out)
    payload = _parse_json_output(out, code="audio_output_set_invalid_json", message="Audio output set returned invalid JSON")
    if not payload.get("ok", False):
        raise NetControlError(code="audio_output_set_failed", message="Failed to set audio output", detail=str(payload.get("error") or err or out))
    return payload


def audio_volume_set(sink_name: str | None, volume_percent: int) -> dict:
    target = (sink_name or "").strip()
    volume = max(0, min(150, int(volume_percent)))
    args = ["set", str(volume)]
    if target:
        args.insert(1, target)
    env = _runtime_env_for_user(getpass.getuser())
    rc, out, err = _run_script("audio_volume_ctl.py", args, timeout=15, use_sudo=False, env=env)
    if rc != 0:
        raise NetControlError(code="audio_volume_failed", message="Failed to set audio volume", detail=err or out)
    payload = _parse_json_output(out, code="audio_volume_invalid_json", message="Audio volume returned invalid JSON")
    if not payload.get("ok", False):
        raise NetControlError(code="audio_volume_failed", message="Failed to set audio volume", detail=str(payload.get("error") or err or out))
    return payload


def set_lan_enabled(enabled: bool, ifname: str = "eth0") -> dict:
    iface = (ifname or "eth0").strip()
    if iface not in _allowed_lan_interfaces():
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


def portal_update(service_name: str = "device-portal.service", update_source: str = "") -> dict:
    repo_dir = str(REPO_ROOT.resolve())
    service_name = (service_name or "device-portal.service").strip() or "device-portal.service"
    service_user = _service_user_from_systemd(service_name) or getpass.getuser()
    update_dir = str(_update_dir())
    source = (update_source or "").strip()
    rc, out, err = _run_script(
        "portal_update.sh",
        ["start", repo_dir, service_user, service_name, update_dir, source],
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
        "update_source": parsed.get("update_source", source),
        "git_status": parsed.get("git_status", "unknown"),
        "restart_scheduled": parsed.get("restart_scheduled", "false").lower() == "true",
        "job_id": parsed.get("job_id", ""),
        "status": "running",
        "started_at": parsed.get("started_at", ""),
        "player_update_triggered": parsed.get("player_update_triggered", "false").lower() == "true",
        "player_update_job_id": parsed.get("player_update_job_id", ""),
        "player_update_reason": parsed.get("player_update_reason", ""),
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
        "player_update_triggered": parsed.get("player_update_triggered", "false").lower() == "true",
        "player_update_job_id": parsed.get("player_update_job_id", ""),
        "player_update_reason": parsed.get("player_update_reason", ""),
        "player_update_needed": parsed.get("player_update_needed", "unknown"),
        "player_update_repo": parsed.get("player_update_repo", ""),
        "player_update_service_name": parsed.get("player_update_service_name", ""),
        "player_update_service_user": parsed.get("player_update_service_user", ""),
        "player_update_error": parsed.get("player_update_error", ""),
        "message": message,
        "log": _tail_file(log_file, max_bytes=max_log_bytes) if log_file.exists() else "",
        "log_file": str(log_file),
    }


def player_update(player_repo_link: str, service_user: str = "", service_name: str = "joormann-media-deviceplayer.service") -> dict:
    repo_link = (player_repo_link or "").strip()
    if not repo_link:
        raise NetControlError(code="player_repo_missing", message="Player repo link/path is required")

    service = (service_name or "joormann-media-deviceplayer.service").strip() or "joormann-media-deviceplayer.service"
    user = (service_user or "").strip() or _service_user_from_systemd("device-portal.service") or getpass.getuser()
    update_dir = str(_player_update_dir())

    rc, out, err = _run_script(
        "player_update.sh",
        ["start", repo_link, user, service, update_dir, str(REPO_ROOT.resolve())],
        timeout=25,
        use_sudo=True,
    )
    parsed = _parse_kv_output(out)
    detail = parsed.get("details") or err or out
    if rc != 0:
        raise NetControlError(
            code=parsed.get("code", "player_update_failed"),
            message=parsed.get("message", "Player update failed"),
            detail=detail,
        )

    return {
        "success": parsed.get("success", "true").lower() == "true",
        "repo_dir": parsed.get("repo_dir", ""),
        "repo_link": parsed.get("repo_link", repo_link),
        "service_user": parsed.get("service_user", user),
        "service_name": parsed.get("service_name", service),
        "job_id": parsed.get("job_id", ""),
        "status": "running",
        "message": parsed.get("message", "Player install/update started."),
        "details": detail,
    }


def player_service_install(repo_dir: str, service_user: str, service_name: str, portal_dir: str) -> dict:
    repo_dir = (repo_dir or "").strip()
    service_user = (service_user or "").strip()
    service_name = (service_name or "").strip() or "joormann-media-deviceplayer.service"
    portal_dir = (portal_dir or "").strip()
    if not repo_dir:
        raise NetControlError(code="invalid_payload", message="repo_dir is required")
    if not service_user:
        raise NetControlError(code="invalid_payload", message="service_user is required")
    if not portal_dir:
        raise NetControlError(code="invalid_payload", message="portal_dir is required")

    rc, out, err = _run_script(
        "player_service_install.sh",
        [repo_dir, service_user, service_name, portal_dir],
        timeout=30,
        use_sudo=True,
    )
    parsed = _parse_kv_output(out)
    detail = parsed.get("details") or parsed.get("message") or err or out
    if rc != 0 or parsed.get("success", "false").lower() != "true":
        raise NetControlError(
            code=parsed.get("code", "player_service_install_failed"),
            message=parsed.get("message", "Player service install failed"),
            detail=detail,
        )

    return {
        "success": True,
        "service_name": parsed.get("service_name", service_name),
        "repo_dir": parsed.get("repo_dir", repo_dir),
        "portal_dir": parsed.get("portal_dir", portal_dir),
        "service_user": parsed.get("service_user", service_user),
        "active_state": parsed.get("active_state", ""),
        "substate": parsed.get("substate", ""),
        "message": parsed.get("message", "Player service installed"),
    }


def player_update_status(job_id: str = "", max_log_bytes: int = MAX_UPDATE_LOG_BYTES) -> dict:
    updates_dir = _player_update_dir()
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
                "message": "No player update run found yet.",
                "log": "",
            }
        selected_job_id = state_files[0].stem

    state_file = updates_dir / f"{selected_job_id}.state"
    log_file = updates_dir / f"{selected_job_id}.log"
    if not state_file.exists():
        raise NetControlError(code="job_not_found", message="Player update job not found")

    try:
        raw_state = state_file.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        raise NetControlError(code="update_state_read_failed", message="Failed to read player update state", detail=str(exc))
    parsed = _parse_kv_output(raw_state)
    status = (parsed.get("status") or "unknown").strip().lower()
    success = (parsed.get("success") or "false").strip().lower() == "true"
    if status == "done":
        message = "Player update completed."
    elif status == "failed":
        message = "Player update failed."
    elif status == "restarting":
        message = "Player service restart in progress."
    elif status == "running":
        message = "Player update running."
    else:
        message = f"Player update status: {status or 'unknown'}"

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


def system_power_action(action: str) -> dict:
    requested = (action or "").strip().lower()
    if requested not in {"shutdown", "reboot"}:
        raise NetControlError(code="invalid_action", message="Action must be 'shutdown' or 'reboot'")
    rc, out, err = _run_script("system_power.sh", [requested], timeout=15, use_sudo=True)
    parsed = _parse_kv_output(out)
    detail = parsed.get("details") or err or out
    if rc != 0:
        raise NetControlError(
            code=parsed.get("code", "system_power_failed"),
            message=parsed.get("message", "System power action failed"),
            detail=detail,
        )
    return {
        "success": parsed.get("success", "true").lower() == "true",
        "action": parsed.get("action", requested),
        "message": parsed.get("message", "System action accepted"),
    }


def restart_portal_service(service_name: str = "device-portal.service") -> dict:
    service = (service_name or "device-portal.service").strip() or "device-portal.service"
    rc, out, err = _run_script("portal_restart.sh", [service], timeout=20, use_sudo=True)
    parsed = _parse_kv_output(out)
    detail = parsed.get("details") or err or out
    if rc != 0:
        raise NetControlError(
            code=parsed.get("code", "portal_restart_failed"),
            message=parsed.get("message", "Portal restart failed"),
            detail=detail,
        )
    return {
        "success": parsed.get("success", "true").lower() == "true",
        "service_name": parsed.get("service_name", service),
        "message": parsed.get("message", "Portal service restart requested"),
    }


def player_service_action(action: str, service_name: str = "joormann-media-deviceplayer.service") -> dict:
    requested = (action or "").strip().lower()
    if requested not in {"start", "stop", "restart", "status"}:
        raise NetControlError(code="invalid_action", message="Action must be start|stop|restart|status")

    service = (service_name or "joormann-media-deviceplayer.service").strip() or "joormann-media-deviceplayer.service"
    rc, out, err = _run_script("player_service.sh", [requested, service], timeout=25, use_sudo=True)
    parsed = _parse_kv_output(out)
    detail = parsed.get("details") or err or out
    if rc != 0:
        raise NetControlError(
            code=parsed.get("code", "player_service_failed"),
            message=parsed.get("message", "Player service action failed"),
            detail=detail,
        )

    return {
        "success": parsed.get("success", "true").lower() == "true",
        "action": parsed.get("action", requested),
        "service_name": parsed.get("service_name", service),
        "active": parsed.get("active", "").lower() == "true",
        "substate": parsed.get("substate", ""),
        "message": parsed.get("message", "Player service action processed"),
    }


def spotify_connect_service_action(
    action: str,
    service_name: str = "",
    service_user: str = "",
    service_scope: str = "",
    service_candidates: str = "",
) -> dict:
    requested = (action or "").strip().lower()
    if requested not in {"start", "stop", "restart", "enable", "disable", "status", "refresh"}:
        raise NetControlError(code="invalid_action", message="Action must be start|stop|restart|enable|disable|status|refresh")

    script_action = "status" if requested == "refresh" else requested
    args = [script_action]
    service = (service_name or "").strip()
    if service:
        args.append(service)

    env = os.environ.copy()
    if service_user:
        env["SPOTIFY_CONNECT_SERVICE_USER"] = service_user
    if service_scope:
        env["SPOTIFY_CONNECT_SERVICE_SCOPE"] = service_scope
    if service_candidates:
        env["SPOTIFY_CONNECT_SERVICE_CANDIDATES"] = service_candidates

    rc, out, err = _run_script("spotify_connect_service.sh", args, timeout=30, use_sudo=True, env=env)
    parsed = _parse_kv_output(out)
    detail = parsed.get("details") or err or out
    if rc != 0:
        raise NetControlError(
            code=parsed.get("code", "spotify_connect_failed"),
            message=parsed.get("message", "Spotify Connect action failed"),
            detail=detail,
        )

    service_installed = _parse_bool_flag(parsed.get("service_installed"))
    service_enabled = _parse_bool_flag(parsed.get("service_enabled"))
    service_running = _parse_bool_flag(parsed.get("service_running"))
    connect_ready = _parse_bool_flag(parsed.get("connect_ready"))
    return {
        "success": parsed.get("success", "true").lower() == "true",
        "action": parsed.get("action", requested),
        "serviceName": parsed.get("service_name", service),
        "serviceScope": parsed.get("service_scope", ""),
        "serviceInstalled": service_installed is True,
        "serviceEnabled": service_enabled is True,
        "serviceRunning": service_running is True,
        "serviceEnabledState": parsed.get("service_enabled_state", ""),
        "serviceActiveState": parsed.get("service_active_state", ""),
        "serviceSubState": parsed.get("service_sub_state", ""),
        "deviceName": parsed.get("device_name", ""),
        "backend": parsed.get("backend", ""),
        "outputDevice": parsed.get("output_device", ""),
        "lastError": parsed.get("last_error", ""),
        "connectReady": connect_ready is True,
        "checkedAt": parsed.get("checked_at", ""),
        "message": parsed.get("message", "Spotify Connect status processed"),
    }


def spotify_connect_install(
    service_name: str = "",
    service_user: str = "",
    service_scope: str = "",
    service_candidates: str = "",
) -> dict:
    args = ["install"]
    service = (service_name or "").strip()
    if service:
        args.append(service)

    env = os.environ.copy()
    if service_user:
        env["SPOTIFY_CONNECT_SERVICE_USER"] = service_user
    if service_scope:
        env["SPOTIFY_CONNECT_SERVICE_SCOPE"] = service_scope
    if service_candidates:
        env["SPOTIFY_CONNECT_SERVICE_CANDIDATES"] = service_candidates

    rc, out, err = _run_script("spotify_connect_install.sh", args, timeout=300, use_sudo=True, env=env)
    parsed = _parse_kv_output(out)
    detail = parsed.get("details") or err or out
    if rc != 0:
        raise NetControlError(
            code=parsed.get("code", "spotify_connect_install_failed"),
            message=parsed.get("message", "Spotify Connect install failed"),
            detail=detail,
        )
    return {
        "success": parsed.get("success", "true").lower() == "true",
        "action": parsed.get("action", "install"),
        "serviceName": parsed.get("service_name", service),
        "serviceScope": parsed.get("service_scope", ""),
        "message": parsed.get("message", "Spotify Connect service installed"),
    }
