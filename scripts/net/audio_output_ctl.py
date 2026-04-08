#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys


MAC_FROM_BLUEZ_RE = re.compile(r"bluez_output\.([0-9A-Fa-f_]{17})")
MAC_RE = re.compile(r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}")
TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")
ALSA_NUMID_RE = re.compile(r"numid=(\d+),")
ALSA_CARD_LINE_RE = re.compile(r"^\s*(card|karte)\s+\d+:", re.IGNORECASE)
ALSA_CARD_ID_RE = re.compile(r"^\s*(card|karte)\s+(\d+):", re.IGNORECASE)
ALSA_PERCENT_RE = re.compile(r"\[(\d{1,3})%\]")


def _speaker_hw_to_ui_percent(raw_percent: int | None) -> int | None:
    if raw_percent is None:
        return None
    try:
        raw = max(0, min(100, int(raw_percent)))
    except Exception:
        return None
    if raw <= 0:
        return 0
    # Inverse of local_speaker set curve in audio_volume_ctl.py (power 0.55)
    ui = int(round((raw / 100.0) ** (1.0 / 0.55) * 100.0))
    return max(0, min(100, ui))


def _run(args: list[str], timeout: int = 12) -> tuple[int, str, str]:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def _has(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _user_runtime_env() -> dict[str, str]:
    env = dict(os.environ)
    uid = os.getuid()
    runtime_dir = env.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
    env["XDG_RUNTIME_DIR"] = runtime_dir
    bus_path = f"{runtime_dir}/bus"
    if os.path.exists(bus_path):
        env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={bus_path}")
    pulse_socket = f"{runtime_dir}/pulse/native"
    if os.path.exists(pulse_socket):
        env.setdefault("PULSE_SERVER", f"unix:{pulse_socket}")
    return env


def _run_env(args: list[str], timeout: int = 12, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def _ensure_audio_runtime() -> None:
    env = _user_runtime_env()
    # Try to bring user audio stack online in headless/service sessions.
    if _has("systemctl"):
        _run_env(["systemctl", "--user", "start", "wireplumber.service"], timeout=4, env=env)
        _run_env(["systemctl", "--user", "start", "pipewire.service"], timeout=4, env=env)
        _run_env(["systemctl", "--user", "start", "pipewire-pulse.service"], timeout=4, env=env)
    if _has("pulseaudio"):
        _run_env(["pulseaudio", "--start"], timeout=4, env=env)


def _norm(value: str) -> str:
    return (value or "").strip().lower()


def _token_set(value: str) -> set[str]:
    raw = _norm(value)
    if not raw:
        return set()
    return {part for part in TOKEN_SPLIT_RE.split(raw) if part}


def _is_hdmi_like(sink_name: str, sink_desc: str) -> bool:
    token = f"{sink_name} {sink_desc}".lower()
    if "hdmi" in token or "displayport" in token:
        return True
    tokens = _token_set(token)
    if {"dp", "monitor"} & tokens:
        return True
    # PipeWire/Pulse names often encode HDMI as ...hdmi-stereo...
    if "hdmistereo" in token.replace("-", "").replace("_", ""):
        return True
    return False


def _is_speaker_like(sink_name: str, sink_desc: str) -> bool:
    token = f"{sink_name} {sink_desc}".lower()
    if "auto_null" in token or "null output" in token or "dummy output" in token:
        return False
    if any(marker in token for marker in ("analog", "speaker", "headphone", "headset", "lineout", "mailbox")):
        return True
    if any(marker in token for marker in ("built-in", "builtin", "usb audio", "usb-audio", "usb_audio")):
        return True
    return False


def _extract_mac(value: str) -> str:
    match = MAC_FROM_BLUEZ_RE.search(value or "")
    if not match:
        match = MAC_RE.search(value or "")
        if not match:
            return ""
        return match.group(0).upper()
    return match.group(1).replace("_", ":").upper()


def _collect_sinks_pactl() -> tuple[str, list[dict]]:
    env = _user_runtime_env()
    code, short_out, _ = _run_env(["pactl", "list", "short", "sinks"], timeout=8, env=env)
    if code != 0:
        return "", []
    # If no sinks are visible, try to ensure a user session exists via pulseaudio --check.
    if not short_out.strip():
        _ensure_audio_runtime()
        code_retry, short_out, _ = _run_env(["pactl", "list", "short", "sinks"], timeout=8, env=env)
        if code_retry != 0:
            return "", []
    sinks: list[dict] = []
    for line in short_out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        sinks.append({"index": parts[0].strip(), "name": parts[1].strip()})

    code_info, info_out, _ = _run_env(["pactl", "info"], timeout=8, env=env)
    default_sink = ""
    if code_info == 0:
        for line in info_out.splitlines():
            if line.lower().startswith("default sink:"):
                default_sink = line.split(":", 1)[1].strip()
                break

    # optional friendly descriptions
    code_long, long_out, _ = _run_env(["pactl", "list", "sinks"], timeout=12, env=env)
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
            if current_name and (stripped.startswith("Volume:") or stripped.startswith("Lautstärke:")):
                # Example: Volume: front-left: 65536 / 100% / 0.00 dB, front-right: 65536 / 100% / 0.00 dB
                if "%" in stripped:
                    percent_raw = stripped.split("%", 1)[0]
                    percent_token = percent_raw.split()[-1]
                    try:
                        percent = int(percent_token)
                    except Exception:
                        percent = None
                    if percent is not None:
                        for sink in sinks:
                            if sink["name"] == current_name and "volume_percent" not in sink:
                                sink["volume_percent"] = percent
                                break

    if not default_sink and sinks:
        # Try to pick a reasonable default if Pulse hasn't set one.
        preferred = ""
        for sink in sinks:
            name = str(sink.get("name") or "")
            desc = str(sink.get("description") or name)
            token = f"{name} {desc}".lower()
            if any(marker in token for marker in ("analog", "speaker", "headphone", "lineout", "mailbox")):
                preferred = name
                break
        if not preferred:
            for sink in sinks:
                name = str(sink.get("name") or "")
                desc = str(sink.get("description") or name)
                if "hdmi" in f"{name} {desc}".lower():
                    preferred = name
                    break
        if not preferred:
            preferred = str(sinks[0].get("name") or "")
        if preferred:
            _run_env(["pactl", "set-default-sink", preferred], timeout=8, env=env)
            default_sink = preferred
    return default_sink, sinks


def _collect_sinks_wpctl() -> tuple[str, list[dict]]:
    if not _has("wpctl"):
        return "", []

    env = _user_runtime_env()
    code_def, def_out, _ = _run_env(["wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"], timeout=8, env=env)
    default_sink = ""
    if code_def == 0:
        for line in def_out.splitlines():
            stripped = line.strip()
            if stripped.startswith("node.name"):
                parts = stripped.split("=", 1)
                if len(parts) == 2:
                    default_sink = parts[1].strip().strip('"')
                    break

    code, status_out, _ = _run_env(["wpctl", "status", "-n"], timeout=8, env=env)
    if code != 0:
        return default_sink, []
    if not status_out.strip():
        _ensure_audio_runtime()
        code, status_out, _ = _run_env(["wpctl", "status", "-n"], timeout=8, env=env)
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
        is_dummy = ("auto_null" in token or "null output" in token or "dummy output" in token)
        if is_dummy:
            continue
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
                    "volume_percent": sink.get("volume_percent"),
                }
            )
        if _is_hdmi_like(sink_name, sink_desc):
            hdmi_candidates.append({"sink_name": sink_name, "description": sink_desc, "volume_percent": sink.get("volume_percent")})
        if _is_speaker_like(sink_name, sink_desc):
            speaker_candidates.append({"sink_name": sink_name, "description": sink_desc, "volume_percent": sink.get("volume_percent")})

    if not speaker_candidates:
        for sink in sinks:
            sink_name = str(sink.get("name") or "")
            sink_desc = str(sink.get("description") or sink_name)
            token = f"{sink_name} {sink_desc}".lower()
            if "auto_null" in token or "null output" in token or "dummy output" in token:
                continue
            if "bluez_output" in token or "bluetooth" in token:
                continue
            if _is_hdmi_like(sink_name, sink_desc):
                continue
            speaker_candidates.append({"sink_name": sink_name, "description": sink_desc, "volume_percent": sink.get("volume_percent")})
            break

    local_hdmi = {
        "id": "local_hdmi",
        "label": "HDMI",
        "type": "local",
        "available": len(hdmi_candidates) > 0,
        "sink_name": (hdmi_candidates[0]["sink_name"] if hdmi_candidates else ""),
        "volume_percent": (hdmi_candidates[0].get("volume_percent") if hdmi_candidates else None),
    }
    local_speaker = {
        "id": "local_speaker",
        "label": "Lokale Lautsprecher / Klinke",
        "type": "local",
        "available": len(speaker_candidates) > 0,
        "sink_name": (speaker_candidates[0]["sink_name"] if speaker_candidates else ""),
        "volume_percent": (speaker_candidates[0].get("volume_percent") if speaker_candidates else None),
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
    env = _user_runtime_env()
    code, out, _ = _run_env(["pactl", "list", "short", "sink-inputs"], timeout=8, env=env)
    if code != 0:
        return
    for line in out.splitlines():
        parts = line.split("\t")
        if not parts or not parts[0].strip().isdigit():
            continue
        sink_input_id = parts[0].strip()
        _run_env(["pactl", "move-sink-input", sink_input_id, sink_name], timeout=8, env=env)


def _find_bluetooth_sink(mac: str, sinks: list[dict]) -> str:
    needle = mac.replace(":", "_").upper()
    for sink in sinks:
        name = str(sink.get("name") or "")
        if needle and needle in name.upper():
            return name
    return ""


def _collect_alsa_caps() -> dict:
    caps = {
        "ok": False,
        "hdmi_available": False,
        "speaker_available": False,
        "cards": [],
        "speaker_volume_percent": None,
        "hdmi_volume_percent": None,
        "selector_numid": None,
        "current_output": "",
    }
    if not _has("aplay"):
        return caps
    code, out, _ = _run(["aplay", "-l"], timeout=8)
    if code != 0:
        return caps
    caps["ok"] = True
    rows: list[dict] = []
    speaker_cards: list[int] = []
    hdmi_cards: list[int] = []
    for line in out.splitlines():
        raw = line.strip()
        if not ALSA_CARD_LINE_RE.match(raw):
            continue
        # Example: card 1: NVidia [HDA NVidia], device 3: HDMI 0 [Panasonic-TV]
        rows.append({"raw": raw})
        token = raw.lower()
        m_id = ALSA_CARD_ID_RE.match(raw)
        card_id = int(m_id.group(2)) if m_id else None
        if "hdmi" in token or "displayport" in token or "vc4hdmi" in token:
            caps["hdmi_available"] = True
            if card_id is not None and card_id not in hdmi_cards:
                hdmi_cards.append(card_id)
        if any(marker in token for marker in ("headphone", "headphones", "analog", "speaker", "line out", "lineout", "mailbox")):
            caps["speaker_available"] = True
            if card_id is not None and card_id not in speaker_cards:
                speaker_cards.append(card_id)
    caps["cards"] = rows

    if _has("amixer"):
        def _controls_for_card(card_id: int | None) -> list[str]:
            args = ["amixer"]
            if card_id is not None:
                args.extend(["-c", str(card_id)])
            args.append("scontrols")
            code_ctrl, out_ctrl, _ = _run(args, timeout=8)
            if code_ctrl != 0:
                return []
            controls: list[str] = []
            for line in out_ctrl.splitlines():
                m = re.search(r"'([^']+)'", line)
                if m:
                    controls.append(m.group(1))
            return controls

        def _read_control_volume(card_id: int | None, control_name: str) -> int | None:
            args = ["amixer"]
            if card_id is not None:
                args.extend(["-c", str(card_id)])
            args.extend(["sget", control_name])
            code_get, out_get, _ = _run(args, timeout=8)
            if code_get != 0:
                return None
            values = ALSA_PERCENT_RE.findall(out_get)
            if not values:
                return None
            parsed: list[int] = []
            for raw in values:
                try:
                    parsed.append(max(0, min(150, int(raw))))
                except Exception:
                    continue
            if not parsed:
                return None
            return max(parsed)

        def _volume_for_card(card_id: int | None) -> int | None:
            controls = _controls_for_card(card_id)
            if not controls:
                return None
            preferred = ["PCM", "Master", "Speaker", "Headphone", "Line", "Digital", "Playback"]
            include_tokens = ("pcm", "master", "speaker", "headphone", "line", "digital", "playback", "front", "surround", "center", "lfe", "side")
            exclude_tokens = ("capture", "mic", "boost", "input", "loopback")
            ordered: list[str] = []
            used = set()
            for name in preferred:
                for ctl in controls:
                    low = ctl.lower()
                    if (
                        (low == name.lower() or name.lower() in low)
                        and not any(token in low for token in exclude_tokens)
                        and ctl not in used
                    ):
                        ordered.append(ctl)
                        used.add(ctl)
            for ctl in controls:
                if ctl not in used:
                    low = ctl.lower()
                    if any(token in low for token in include_tokens) and not any(token in low for token in exclude_tokens):
                        ordered.append(ctl)
                        used.add(ctl)
            # Use the highest relevant playback volume as user-visible channel volume.
            # This mirrors the write path where we set multiple controls.
            best: int | None = None
            for ctl in ordered:
                vol = _read_control_volume(card_id, ctl)
                if vol is None:
                    continue
                if best is None or vol > best:
                    best = vol
            return best

        for cid in speaker_cards:
            vol = _volume_for_card(cid)
            if vol is not None:
                caps["speaker_volume_percent"] = _speaker_hw_to_ui_percent(vol)
                break
        for cid in hdmi_cards:
            vol = _volume_for_card(cid)
            if vol is not None:
                caps["hdmi_volume_percent"] = vol
                break

        # Try to detect Raspberry Pi output selector (legacy control).
        code_ctrls, ctrls_out, _ = _run(["amixer", "controls"], timeout=8)
        if code_ctrls == 0:
            selector = None
            for line in ctrls_out.splitlines():
                low = line.lower()
                if "name='3d control - output'" in low or "name='output'" in low:
                    match = ALSA_NUMID_RE.search(line)
                    if match:
                        selector = match.group(1)
                        break
            if selector:
                caps["selector_numid"] = selector
                code_get, get_out, _ = _run(["amixer", "cget", f"numid={selector}"], timeout=8)
                if code_get == 0:
                    m = re.search(r"values=(\d+)", get_out)
                    if m:
                        value = int(m.group(1))
                        # Raspberry Pi mapping: 1=analog, 2=hdmi
                        if value == 2:
                            caps["current_output"] = "local_hdmi"
                        elif value == 1:
                            caps["current_output"] = "local_speaker"
    return caps


def _alsa_set_output(output_id: str, caps: dict) -> dict:
    wanted = (output_id or "").strip()
    selector = str(caps.get("selector_numid") or "").strip()
    if not _has("amixer") or not selector:
        raise RuntimeError("ALSA output selector not available")
    if wanted == "local_hdmi":
        value = "2"
    elif wanted == "local_speaker":
        value = "1"
    else:
        raise RuntimeError(f"unsupported ALSA output target: {wanted}")
    code, out, err = _run(["amixer", "-q", "cset", f"numid={selector}", value], timeout=8)
    if code != 0:
        raise RuntimeError(err or out or "amixer cset failed")
    return {"output": wanted, "sink_name": "", "backend": "alsa", "selector_numid": selector}


def _select_output(target_output: str, available_outputs: list[dict], sinks: list[dict], alsa_caps: dict | None = None) -> dict:
    wanted = (target_output or "").strip()
    if not wanted:
        raise ValueError("output is required")
    selected = None
    for item in available_outputs:
        if str(item.get("id") or "") == wanted:
            selected = item
            break
    if selected is None and wanted.startswith("bluetooth:"):
        mac = wanted.split(":", 1)[1].strip().upper()
        sink_name = _find_bluetooth_sink(mac, sinks)
        if sink_name:
            selected = {"id": wanted, "available": True, "sink_name": sink_name}
    if selected is None:
        raise ValueError(f"output not available: {wanted}")
    if not selected.get("available"):
        raise ValueError(f"output currently unavailable: {wanted}")

    sink_name = str(selected.get("sink_name") or "").strip()
    if not sink_name and wanted.startswith("bluetooth:"):
        mac = wanted.split(":", 1)[1].strip().upper()
        sink_name = _find_bluetooth_sink(mac, sinks)
    if not sink_name:
        alsa = alsa_caps if isinstance(alsa_caps, dict) else {}
        if wanted in ("local_hdmi", "local_speaker") and bool(alsa.get("ok")):
            # Raspberry Pi systems may expose HDMI/Klinke via ALSA cards but without
            # a dedicated "output selector" control. In that case we treat selection
            # as a logical profile switch and do not hard-fail.
            if str(alsa.get("selector_numid") or "").strip():
                return _alsa_set_output(wanted, alsa)
            return {
                "output": wanted,
                "sink_name": "",
                "backend": "alsa",
                "selector_numid": "",
                "note": "alsa_output_selector_missing",
            }
        raise ValueError("sink mapping missing")

    if _has("pactl"):
        env = _user_runtime_env()
        code, _, err = _run_env(["pactl", "set-default-sink", sink_name], timeout=8, env=env)
        if code != 0:
            raise RuntimeError(err or "pactl set-default-sink failed")
        _move_streams_to_sink(sink_name)
        return {"output": wanted, "sink_name": sink_name, "backend": "pactl"}

    if _has("wpctl"):
        env = _user_runtime_env()
        code, _, err = _run_env(["wpctl", "set-default", sink_name], timeout=8, env=env)
        if code != 0:
            raise RuntimeError(err or "wpctl set-default failed")
        return {"output": wanted, "sink_name": sink_name, "backend": "wpctl"}

    raise RuntimeError("neither pactl nor wpctl available")


def main() -> int:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "status").strip().lower()
    try:
        has_pactl = _has("pactl")
        has_wpctl = _has("wpctl")
        if _has("pactl"):
            default_sink, sinks = _collect_sinks_pactl()
            backend = "pactl"
            # Some hosts expose sinks only via PipeWire/wpctl in the current runtime.
            # If pactl exists but yields no sinks, fallback to wpctl probing.
            if not sinks and _has("wpctl"):
                wp_default, wp_sinks = _collect_sinks_wpctl()
                if wp_sinks:
                    default_sink, sinks = wp_default, wp_sinks
                    backend = "wpctl"
        else:
            default_sink, sinks = _collect_sinks_wpctl()
            backend = "wpctl" if sinks else "none"

        base = _classify_outputs(default_sink, sinks)
        alsa_caps = _collect_alsa_caps()
        if bool(alsa_caps.get("ok")):
            # Always merge ALSA capabilities for local outputs.
            # On Raspberry Pi it's common to see only a dummy PipeWire sink (auto_null),
            # while real HDMI/Klinke availability is only visible via ALSA.
            outputs = base.get("available_outputs") if isinstance(base.get("available_outputs"), list) else []
            for item in outputs:
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("id") or "")
                if item_id == "local_hdmi":
                    item["available"] = bool(item.get("available")) or bool(alsa_caps.get("hdmi_available"))
                    if item.get("volume_percent") is None and alsa_caps.get("hdmi_volume_percent") is not None:
                        item["volume_percent"] = int(alsa_caps.get("hdmi_volume_percent"))
                    if item.get("sink_name") is None:
                        item["sink_name"] = ""
                elif item_id == "local_speaker":
                    item["available"] = bool(item.get("available")) or bool(alsa_caps.get("speaker_available"))
                    if item.get("volume_percent") is None and alsa_caps.get("speaker_volume_percent") is not None:
                        item["volume_percent"] = int(alsa_caps.get("speaker_volume_percent"))
                    if item.get("sink_name") is None:
                        item["sink_name"] = ""
            if not base.get("current_output") and str(alsa_caps.get("current_output") or "").strip():
                base["current_output"] = str(alsa_caps.get("current_output") or "").strip()

        if mode == "status":
            payload = {"ok": True, "backend": backend, **base}
            payload["diagnostics"] = {
                "has_pactl": has_pactl,
                "has_wpctl": has_wpctl,
                "has_aplay": _has("aplay"),
                "has_amixer": _has("amixer"),
                "sink_count": len(sinks),
                "alsa": alsa_caps,
            }
            if backend == "none":
                payload["warning"] = "Kein Audio-Backend gefunden (pactl/wpctl fehlen)."
            print(json.dumps(payload))
            return 0

        if mode == "set":
            if len(sys.argv) < 3:
                raise ValueError("target output required")
            result = _select_output(sys.argv[2], base.get("available_outputs", []), sinks, alsa_caps=alsa_caps)
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
