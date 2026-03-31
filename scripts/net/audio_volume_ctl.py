#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
import sys


def _run(args: list[str], timeout: int = 8) -> tuple[int, str, str]:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def _has(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _default_sink_pactl() -> str:
    code, out, _ = _run(["pactl", "info"], timeout=6)
    if code != 0:
        return ""
    for line in out.splitlines():
        if line.lower().startswith("default sink:"):
            return line.split(":", 1)[1].strip()
    return ""


def _set_volume_pactl(sink: str, volume: int) -> tuple[bool, str]:
    target = sink or _default_sink_pactl()
    if not target:
        return False, "no default sink"
    code, _, err = _run(["pactl", "set-sink-volume", target, f"{volume}%"], timeout=8)
    if code != 0:
        return False, err or "pactl set-sink-volume failed"
    return True, target


def _set_volume_wpctl(sink: str, volume: int) -> tuple[bool, str]:
    target = sink or "@DEFAULT_AUDIO_SINK@"
    level = max(0.0, min(1.5, volume / 100.0))
    code, _, err = _run(["wpctl", "set-volume", target, f"{level:.2f}"], timeout=8)
    if code != 0:
        return False, err or "wpctl set-volume failed"
    return True, target


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] != "set":
        print(json.dumps({"ok": False, "error": "usage: audio_volume_ctl.py set [sink_name] <percent>"}))
        return 2

    if len(sys.argv) == 3:
        sink = ""
        raw = sys.argv[2]
    else:
        sink = sys.argv[2]
        raw = sys.argv[3] if len(sys.argv) > 3 else ""

    try:
        volume = int(float(raw))
    except Exception:
        print(json.dumps({"ok": False, "error": "volume must be numeric"}))
        return 2

    volume = max(0, min(150, volume))

    if _has("pactl"):
        ok, target = _set_volume_pactl(sink, volume)
        backend = "pactl"
    elif _has("wpctl"):
        ok, target = _set_volume_wpctl(sink, volume)
        backend = "wpctl"
    else:
        print(json.dumps({"ok": False, "error": "no pactl/wpctl available"}))
        return 3

    if not ok:
        print(json.dumps({"ok": False, "error": "volume set failed", "sink": target}))
        return 1

    print(json.dumps({"ok": True, "sink": target, "volume_percent": volume, "backend": backend}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
