#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys


MAC_FROM_BLUEZ_RE = re.compile(r"bluez_output\.([0-9A-Fa-f_]{17})")


def _run(args: list[str], timeout: int = 12) -> tuple[int, str, str]:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def _has(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _norm(value: str) -> str:
    return (value or "").strip().lower()


def _extract_mac(value: str) -> str:
    match = MAC_FROM_BLUEZ_RE.search(value or "")
    if not match:
        return ""
    return match.group(1).replace("_", ":").upper()


def _collect_sinks_pactl() -> tuple[str, list[dict]]:
    code, short_out, _ = _run(["pactl", "list", "short", "sinks"], timeout=8)
    if code != 0:
        return "", []
    # If no sinks are visible, try to ensure a user session exists via pulseaudio --check.
    if not short_out.strip():
        _run(["pulseaudio", "--check"], timeout=3)
        code_retry, short_out, _ = _run(["pactl", "list", "short", "sinks"], timeout=8)
        if code_retry != 0:
            return "", []
    sinks: list[dict] = []
    for line in short_out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        sinks.append({"index": parts[0].strip(), "name": parts[1].strip()})

    code_info, info_out, _ = _run(["pactl", "info"], timeout=8)
    default_sink = ""
    if code_info == 0:
        for line in info_out.splitlines():
            if line.lower().startswith("default sink:"):
                default_sink = line.split(":", 1)[1].strip()
                break

    # optional friendly descriptions
    code_long, long_out, _ = _run(["pactl", "list", "sinks"], timeout=12)
    if code_long == 0:
        current_name = ""
        for line in long_out.splitlines():
            stripped = line.strip()
            if stripped.startswith("Name:"):
                current_name = stripped.split(":", 1)[1].strip()
                continue
            if stripped.startswith("Description:") and current_name:
                desc = stripped.split(":", 1)[1].strip()
                for sink in sinks:
                    if sink["name"] == current_name and "description" not in sink:
                        sink["description"] = desc
                        break
    return default_sink, sinks


def _collect_sinks_wpctl() -> tuple[str, list[dict]]:
    if not _has("wpctl"):
        return "", []

    code_def, def_out, _ = _run(["wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"], timeout=8)
    default_sink = ""
    if code_def == 0:
        for line in def_out.splitlines():
            stripped = line.strip()
            if stripped.startswith("node.name"):
                parts = stripped.split("=", 1)
                if len(parts) == 2:
                    default_sink = parts[1].strip().strip('"')
                    break

    code, status_out, _ = _run(["wpctl", "status", "-n"], timeout=8)
    if code != 0:
        return default_sink, []
    sinks: list[dict] = []
    in_sinks = False
    for line in status_out.splitlines():
        stripped = line.strip()
        if stripped.startswith("Sinks:"):
            in_sinks = True
            continue
        if in_sinks and stripped.startswith("Sources:"):
            break
        if not in_sinks:
            continue
        if "." not in stripped:
            continue
        # Example: 46. alsa_output.pci-... [vol: ...]
        prefix, _, rest = stripped.partition(".")
        idx = prefix.strip()
        name = rest.strip().split(" [", 1)[0].strip()
        if not idx.isdigit() or not name:
            continue
        sinks.append({"index": idx, "name": name, "description": name})
    return default_sink, sinks


def _classify_outputs(default_sink: str, sinks: list[dict]) -> dict:
    available_outputs: list[dict] = []
    current_output = ""
    hdmi_candidates: list[dict] = []
    speaker_candidates: list[dict] = []
    bluetooth_candidates: list[dict] = []

    for sink in sinks:
        sink_name = str(sink.get("name") or "")
        sink_desc = str(sink.get("description") or sink_name)
        token = f"{sink_name} {sink_desc}".lower()
        if "bluez_output" in token or "bluetooth" in token:
            mac = _extract_mac(sink_name) or _extract_mac(sink_desc) or sink_name
            bluetooth_candidates.append(
                {
                    "id": f"bluetooth:{mac}",
                    "label": sink_desc,
                    "type": "bluetooth",
                    "available": True,
                    "connected": True,
                    "sink_name": sink_name,
                }
            )
        if "hdmi" in token:
            hdmi_candidates.append({"sink_name": sink_name, "description": sink_desc})
        if any(marker in token for marker in ("analog", "speaker", "headphone", "headset", "lineout")):
            speaker_candidates.append({"sink_name": sink_name, "description": sink_desc})

    local_hdmi = {
        "id": "local_hdmi",
        "label": "HDMI",
        "type": "local",
        "available": len(hdmi_candidates) > 0,
        "sink_name": (hdmi_candidates[0]["sink_name"] if hdmi_candidates else ""),
    }
    local_speaker = {
        "id": "local_speaker",
        "label": "Lokale Lautsprecher / Klinke",
        "type": "local",
        "available": len(speaker_candidates) > 0,
        "sink_name": (speaker_candidates[0]["sink_name"] if speaker_candidates else ""),
    }
    available_outputs.extend([local_hdmi, local_speaker])
    available_outputs.extend(bluetooth_candidates)

    if default_sink:
        if local_hdmi.get("sink_name") and local_hdmi["sink_name"] == default_sink:
            current_output = "local_hdmi"
        elif local_speaker.get("sink_name") and local_speaker["sink_name"] == default_sink:
            current_output = "local_speaker"
        else:
            for bt in bluetooth_candidates:
                if bt.get("sink_name") == default_sink:
                    current_output = str(bt.get("id") or "")
                    break
    return {"current_output": current_output, "available_outputs": available_outputs}


def _move_streams_to_sink(sink_name: str) -> None:
    code, out, _ = _run(["pactl", "list", "short", "sink-inputs"], timeout=8)
    if code != 0:
        return
    for line in out.splitlines():
        parts = line.split("\t")
        if not parts or not parts[0].strip().isdigit():
            continue
        sink_input_id = parts[0].strip()
        _run(["pactl", "move-sink-input", sink_input_id, sink_name], timeout=8)


def _select_output(target_output: str, available_outputs: list[dict]) -> dict:
    wanted = (target_output or "").strip()
    if not wanted:
        raise ValueError("output is required")
    selected = None
    for item in available_outputs:
        if str(item.get("id") or "") == wanted:
            selected = item
            break
    if selected is None:
        raise ValueError(f"output not available: {wanted}")
    if not selected.get("available"):
        raise ValueError(f"output currently unavailable: {wanted}")

    sink_name = str(selected.get("sink_name") or "").strip()
    if not sink_name:
        raise ValueError("sink mapping missing")

    if _has("pactl"):
        code, _, err = _run(["pactl", "set-default-sink", sink_name], timeout=8)
        if code != 0:
            raise RuntimeError(err or "pactl set-default-sink failed")
        _move_streams_to_sink(sink_name)
        return {"output": wanted, "sink_name": sink_name, "backend": "pactl"}

    if _has("wpctl"):
        code, _, err = _run(["wpctl", "set-default", sink_name], timeout=8)
        if code != 0:
            raise RuntimeError(err or "wpctl set-default failed")
        return {"output": wanted, "sink_name": sink_name, "backend": "wpctl"}

    raise RuntimeError("neither pactl nor wpctl available")


def main() -> int:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "status").strip().lower()
    try:
        if _has("pactl"):
            default_sink, sinks = _collect_sinks_pactl()
            backend = "pactl"
        else:
            default_sink, sinks = _collect_sinks_wpctl()
            backend = "wpctl" if sinks else "none"

        base = _classify_outputs(default_sink, sinks)

        if mode == "status":
            payload = {"ok": True, "backend": backend, **base}
            print(json.dumps(payload))
            return 0

        if mode == "set":
            if len(sys.argv) < 3:
                raise ValueError("target output required")
            result = _select_output(sys.argv[2], base.get("available_outputs", []))
            if _has("pactl"):
                default_sink, sinks = _collect_sinks_pactl()
            else:
                default_sink, sinks = _collect_sinks_wpctl()
            post = _classify_outputs(default_sink, sinks)
            payload = {"ok": True, "backend": backend, "result": result, **post}
            print(json.dumps(payload))
            return 0

        raise ValueError("unsupported mode")
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc), "mode": mode}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
