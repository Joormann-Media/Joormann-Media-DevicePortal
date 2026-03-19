#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass


MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
DEVICE_RE = re.compile(r"^Device\s+(([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})\s*(.*)$")
ANSI_RE = re.compile(r"\x1B\[[0-9;]*[A-Za-z]")
UUID_AUDIO_HINTS = (
    "audio sink",
    "a2dp",
    "headset",
    "handsfree",
    "avrcp",
)


@dataclass
class BtResult:
    rc: int
    out: str
    err: str


def _clean(raw: str) -> str:
    return ANSI_RE.sub("", raw or "").replace("\r", "")


def _btctl_path() -> str:
    path = shutil.which("bluetoothctl")
    if not path:
        raise RuntimeError("bluetoothctl not found")
    return path


def _adapter_exists() -> bool:
    try:
        root = Path("/sys/class/bluetooth")
        if not root.exists():
            return False
        return any(item.name.startswith("hci") for item in root.iterdir())
    except Exception:
        return False


def _run(args: list[str], timeout: int = 20) -> BtResult:
    btctl = _btctl_path()
    cmd = [btctl]
    if "--timeout" not in args:
        cmd.extend(["--timeout", str(max(2, min(int(timeout), 30)))])
    cmd.extend(args)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 6)
        return BtResult(proc.returncode, _clean(proc.stdout), _clean(proc.stderr))
    except subprocess.TimeoutExpired as exc:
        return BtResult(124, "", f"timeout: {exc}")


def _bool_from_info(info: str, key: str) -> bool:
    for line in info.splitlines():
        line = line.strip()
        if line.lower().startswith(f"{key.lower()}:"):
            value = line.split(":", 1)[1].strip().lower()
            return value in ("yes", "true", "on", "1")
    return False


def _extract_alias(info: str) -> str:
    for line in info.splitlines():
        line = line.strip()
        if line.startswith("Alias:"):
            return line.split(":", 1)[1].strip()
    return ""


def _audio_capable(info: str) -> bool:
    for line in info.splitlines():
        line = line.strip().lower()
        if not line.startswith("uuid:"):
            continue
        if any(hint in line for hint in UUID_AUDIO_HINTS):
            return True
    return False


def _parse_devices(raw: str) -> list[tuple[str, str]]:
    devices: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        line = line.strip()
        match = DEVICE_RE.match(line)
        if not match:
            continue
        mac = match.group(1).upper()
        name = (match.group(3) or "").strip()
        if mac in seen:
            continue
        seen.add(mac)
        devices.append((mac, name))
    return devices


def _list_paired_set() -> set[str]:
    try:
        paired = _run(["paired-devices"], timeout=20)
        if paired.rc != 0:
            return set()
        return {mac for mac, _ in _parse_devices(paired.out)}
    except Exception:
        return set()


def _collect_devices() -> list[dict]:
    listed = _run(["devices"], timeout=10)
    if listed.rc != 0:
        raise RuntimeError((listed.err or listed.out or "bluetoothctl devices failed").strip())
    paired_set = _list_paired_set()
    rows: list[dict] = []
    for mac, listed_name in _parse_devices(listed.out):
        info = _run(["info", mac], timeout=10)
        info_text = info.out if info.rc == 0 else ""
        alias = _extract_alias(info_text) or listed_name or mac
        rows.append(
            {
                "id": mac,
                "name": alias,
                "paired": mac in paired_set or _bool_from_info(info_text, "Paired"),
                "trusted": _bool_from_info(info_text, "Trusted"),
                "connected": _bool_from_info(info_text, "Connected"),
                "audio_capable": _audio_capable(info_text),
            }
        )
    rows.sort(key=lambda item: (str(item.get("name") or "").lower(), str(item.get("id") or "")))
    return rows


def _validate_mac(value: str) -> str:
    mac = (value or "").strip().upper()
    if not MAC_RE.match(mac):
        raise ValueError("invalid bluetooth mac")
    return mac


def _run_action(action: str, mac: str) -> dict:
    target = _validate_mac(mac)
    if action == "pair":
        _run(["power", "on"], timeout=8)
        pair = _run(["pair", target], timeout=40)
        trust = _run(["trust", target], timeout=20)
        connect = _run(["connect", target], timeout=30)
        return {
            "action": action,
            "device_id": target,
            "pair_rc": pair.rc,
            "trust_rc": trust.rc,
            "connect_rc": connect.rc,
            "detail": (pair.err or pair.out or trust.err or connect.err).strip(),
        }
    if action == "connect":
        _run(["power", "on"], timeout=8)
        trust = _run(["trust", target], timeout=20)
        conn = _run(["connect", target], timeout=30)
        return {
            "action": action,
            "device_id": target,
            "trust_rc": trust.rc,
            "connect_rc": conn.rc,
            "detail": (conn.err or conn.out or trust.err).strip(),
        }
    if action == "disconnect":
        dis = _run(["disconnect", target], timeout=20)
        return {
            "action": action,
            "device_id": target,
            "disconnect_rc": dis.rc,
            "detail": (dis.err or dis.out).strip(),
        }
    if action == "forget":
        rm = _run(["remove", target], timeout=20)
        return {
            "action": action,
            "device_id": target,
            "remove_rc": rm.rc,
            "detail": (rm.err or rm.out).strip(),
        }
    raise ValueError("unsupported action")


def _scan(seconds: int) -> dict:
    scan_seconds = max(4, min(30, int(seconds)))
    power = _run(["power", "on"], timeout=8)
    power_text = f"{power.out}\n{power.err}".lower()
    if power.rc != 0 and "succeeded" not in power_text:
        raise RuntimeError((power.err or power.out or "failed to power on bluetooth adapter").strip())

    scan_on = _run(["--timeout", str(scan_seconds), "scan", "on"], timeout=scan_seconds + 10)
    scan_text = f"{scan_on.out}\n{scan_on.err}".lower()
    if scan_on.rc != 0 and "discovery started" not in scan_text:
        raise RuntimeError((scan_on.err or scan_on.out or "failed to scan bluetooth devices").strip())

    # Best-effort cleanup only.
    _run(["scan", "off"], timeout=8)
    return {"scan_seconds": scan_seconds}


def main() -> int:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "devices").strip().lower()
    try:
        if mode in ("scan", "devices", "pair", "connect", "disconnect", "forget") and not _adapter_exists():
            payload = {"ok": True, "mode": mode, "devices": [], "warning": "bluetooth adapter missing"}
            print(json.dumps(payload))
            return 0
        if mode == "scan":
            seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 8
            meta = _scan(seconds)
            payload = {"ok": True, "mode": "scan", "meta": meta, "devices": _collect_devices()}
        elif mode == "devices":
            payload = {"ok": True, "mode": "devices", "devices": _collect_devices()}
        elif mode in ("pair", "connect", "disconnect", "forget"):
            if len(sys.argv) < 3:
                raise ValueError("device mac required")
            action_payload = _run_action(mode, sys.argv[2])
            payload = {"ok": True, "mode": mode, "result": action_payload, "devices": _collect_devices()}
        else:
            raise ValueError("unsupported mode")
    except Exception as exc:
        payload = {"ok": False, "mode": mode, "error": str(exc), "devices": []}
        print(json.dumps(payload))
        return 1

    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
