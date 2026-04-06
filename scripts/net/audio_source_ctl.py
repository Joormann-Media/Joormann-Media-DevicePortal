#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys


def _has(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _runtime_env() -> dict[str, str]:
    env = dict(os.environ)
    uid = os.getuid()
    runtime_dir = env.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
    env["XDG_RUNTIME_DIR"] = runtime_dir
    pulse_socket = f"{runtime_dir}/pulse/native"
    if os.path.exists(pulse_socket):
        env.setdefault("PULSE_SERVER", f"unix:{pulse_socket}")
    bus_path = f"{runtime_dir}/bus"
    if os.path.exists(bus_path):
        env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={bus_path}")
    return env


def _run(args: list[str], timeout: int = 10) -> tuple[int, str, str]:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=_runtime_env())
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def _is_mic_like(name: str, desc: str) -> bool:
    token = f"{name} {desc}".lower()
    if "monitor" in token:
        return False
    return any(k in token for k in ("input", "mic", "microphone", "capture", "headset", "usb"))


def _collect_sources_pactl() -> dict:
    code, out, err = _run(["pactl", "list", "short", "sources"], timeout=8)
    if code != 0:
        return {"ok": False, "error": err or out or "pactl source list failed"}

    rows: list[dict] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        rows.append({"index": parts[0].strip(), "name": parts[1].strip()})

    default_source = ""
    code_info, info_out, _ = _run(["pactl", "info"], timeout=6)
    if code_info == 0:
        for line in info_out.splitlines():
            if line.lower().startswith("default source:"):
                default_source = line.split(":", 1)[1].strip()
                break

    code_long, long_out, _ = _run(["pactl", "list", "sources"], timeout=12)
    if code_long == 0:
        current_name = ""
        for line in long_out.splitlines():
            stripped = line.strip()
            if stripped.startswith("Name:"):
                current_name = stripped.split(":", 1)[1].strip()
                continue
            if stripped.startswith("Description:") and current_name:
                desc = stripped.split(":", 1)[1].strip()
                for row in rows:
                    if row["name"] == current_name and "description" not in row:
                        row["description"] = desc
                        break
            if current_name and (stripped.startswith("Volume:") or stripped.startswith("Lautstärke:")):
                match = re.search(r"(\d+)\s*%", stripped)
                if match:
                    vol = int(match.group(1))
                    for row in rows:
                        if row["name"] == current_name and "volume_percent" not in row:
                            row["volume_percent"] = vol
                            break

    sources: list[dict] = []
    microphones: list[dict] = []
    for row in rows:
        name = str(row.get("name") or "")
        desc = str(row.get("description") or name)
        item = {
            "name": name,
            "description": desc,
            "volume_percent": row.get("volume_percent"),
            "is_default": name == default_source,
            "is_microphone": _is_mic_like(name, desc),
        }
        sources.append(item)
        if item["is_microphone"]:
            microphones.append(item)

    return {
        "ok": True,
        "backend": "pactl",
        "default_source": default_source,
        "sources": sources,
        "microphones": microphones,
    }


def _set_source_volume(source_name: str, volume_percent: int) -> dict:
    if not _has("pactl"):
        return {"ok": False, "error": "pactl not found"}
    if not source_name:
        payload = _collect_sources_pactl()
        if not payload.get("ok"):
            return payload
        source_name = str(payload.get("default_source") or "")
        if not source_name:
            return {"ok": False, "error": "no default source"}
    volume = max(0, min(150, int(volume_percent)))
    code, out, err = _run(["pactl", "set-source-volume", source_name, f"{volume}%"], timeout=8)
    if code != 0:
        return {"ok": False, "error": err or out or "set-source-volume failed"}
    return {"ok": True, "source": source_name, "volume_percent": volume, "backend": "pactl"}


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "usage: audio_source_ctl.py status|set <source_name> <percent>"}))
        return 2
    cmd = sys.argv[1].strip().lower()

    if cmd == "status":
        if _has("pactl"):
            print(json.dumps(_collect_sources_pactl()))
            return 0
        print(json.dumps({"ok": False, "error": "no supported backend (pactl missing)"}))
        return 3

    if cmd == "set":
        if len(sys.argv) < 4:
            print(json.dumps({"ok": False, "error": "usage: audio_source_ctl.py set <source_name> <percent>"}))
            return 2
        source_name = sys.argv[2].strip()
        try:
            volume = int(float(sys.argv[3]))
        except Exception:
            print(json.dumps({"ok": False, "error": "volume must be numeric"}))
            return 2
        result = _set_source_volume(source_name, volume)
        print(json.dumps(result))
        return 0 if result.get("ok") else 1

    print(json.dumps({"ok": False, "error": f"unknown command: {cmd}"}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

