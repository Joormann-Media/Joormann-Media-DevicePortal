#!/usr/bin/env python3
from __future__ import annotations

import json
import re
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


def _first_sink_pactl() -> str:
    code, out, _ = _run(["pactl", "list", "short", "sinks"], timeout=6)
    if code != 0:
        return ""
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1].strip():
            return parts[1].strip()
    return ""


def _set_volume_pactl(sink: str, volume: int) -> tuple[bool, str]:
    target = sink or _default_sink_pactl()
    if not target:
        target = _first_sink_pactl()
        if target:
            _run(["pactl", "set-default-sink", target], timeout=6)
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


ALSA_CARD_LINE_RE = re.compile(r"^\s*(card|karte)\s+(\d+):", re.IGNORECASE)
ALSA_CONTROL_NAME_RE = re.compile(r"'([^']+)'")


def _alsa_cards() -> list[dict]:
    if not _has("aplay"):
        return []
    code, out, _ = _run(["aplay", "-l"], timeout=8)
    if code != 0:
        return []
    cards: list[dict] = []
    for line in out.splitlines():
        raw = line.strip()
        m = ALSA_CARD_LINE_RE.match(raw)
        if not m:
            continue
        card_id = int(m.group(2))
        cards.append({"id": card_id, "raw": raw, "token": raw.lower()})
    return cards


def _alsa_simple_controls(card_id: int | None = None) -> list[str]:
    args = ["amixer"]
    if card_id is not None:
        args.extend(["-c", str(card_id)])
    args.append("scontrols")
    code, out, _ = _run(args, timeout=8)
    if code != 0:
        return []
    controls: list[str] = []
    for line in out.splitlines():
        m = ALSA_CONTROL_NAME_RE.search(line)
        if m:
            controls.append(m.group(1))
    return controls


def _alsa_set_control_volume(volume: int, card_id: int | None = None) -> tuple[bool, str]:
    controls = _alsa_simple_controls(card_id)
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
        if ctl in used:
            continue
        low = ctl.lower()
        if any(token in low for token in include_tokens) and not any(token in low for token in exclude_tokens):
            ordered.append(ctl)
            used.add(ctl)

    successful: list[str] = []
    for ctl in ordered:
        args = ["amixer"]
        if card_id is not None:
            args.extend(["-c", str(card_id)])
        # First try to set volume and clear mute in one step.
        args_unmute = list(args)
        args_unmute.extend(["-q", "sset", ctl, f"{volume}%", "unmute"])
        code, _, _ = _run(args_unmute, timeout=8)
        if code == 0:
            successful.append(ctl)
            continue
        # Fallback for controls without playback switch.
        args_plain = list(args)
        args_plain.extend(["-q", "sset", ctl, f"{volume}%"])
        code, _, _ = _run(args_plain, timeout=8)
        if code == 0:
            successful.append(ctl)

    if not successful:
        # conservative fallback: touch at most one writable control
        for ctl in controls:
            args = ["amixer"]
            if card_id is not None:
                args.extend(["-c", str(card_id)])
            args_unmute = list(args)
            args_unmute.extend(["-q", "sset", ctl, f"{volume}%", "unmute"])
            code, _, _ = _run(args_unmute, timeout=8)
            if code != 0:
                args_plain = list(args)
                args_plain.extend(["-q", "sset", ctl, f"{volume}%"])
                code, _, _ = _run(args_plain, timeout=8)
            if code == 0:
                successful.append(ctl)
                break

    if successful:
        scope = f"card{card_id}" if card_id is not None else "default"
        return True, f"{scope}:{','.join(successful)}"
    return False, "no writable alsa control"


def _set_volume_alsa_output(output_id: str, volume: int) -> tuple[bool, str]:
    output = (output_id or "").strip().lower()
    requested_volume = max(0, min(150, int(volume)))
    # ALSA percentage on analog/klinke is usually not perceptually linear.
    # Use a gentle loudness curve for local_speaker so UI percent matches heard loudness better.
    if output == "local_speaker" and requested_volume > 0:
        normalized = min(requested_volume, 100)
        volume = int(round((normalized / 100.0) ** 0.55 * 100.0))
    else:
        volume = requested_volume
    cards = _alsa_cards()
    speaker_cards: list[int] = []
    hdmi_cards: list[int] = []
    for card in cards:
        token = str(card.get("token") or "")
        cid = int(card.get("id"))
        if any(marker in token for marker in ("hdmi", "vc4hdmi", "displayport")):
            hdmi_cards.append(cid)
        if any(marker in token for marker in ("headphone", "analog", "speaker", "line out", "lineout", "bcm2835")):
            speaker_cards.append(cid)

    candidates: list[int] = []
    if output == "local_hdmi":
        candidates.extend(hdmi_cards)
        candidates.extend(speaker_cards)
    elif output == "local_speaker":
        candidates.extend(speaker_cards)
        candidates.extend(hdmi_cards)
    else:
        candidates.extend(speaker_cards)
        candidates.extend(hdmi_cards)
    # keep order unique
    seen = set()
    ordered = []
    for c in candidates:
        if c in seen:
            continue
        ordered.append(c)
        seen.add(c)

    for card_id in ordered:
        ok, target = _alsa_set_control_volume(volume, card_id=card_id)
        if ok:
            return True, f"alsa:{output or 'default'}:{target}"

    ok, target = _alsa_set_control_volume(volume, card_id=None)
    if ok:
        return True, f"alsa:{output or 'default'}:{target}"
    return False, "alsa volume control failed"


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

    sink_token = (sink or "").strip().lower()
    # Direct ALSA channel handling for hosts without PipeWire sinks.
    if sink_token in ("local_hdmi", "local_speaker"):
        ok, target = _set_volume_alsa_output(sink_token, volume)
        backend = "alsa"
    elif _has("pactl"):
        ok, target = _set_volume_pactl(sink, volume)
        backend = "pactl"
        if not ok and _has("amixer") and "no default sink" in (target or "").lower():
            ok, target = _set_volume_alsa_output("", volume)
            backend = "alsa"
    elif _has("wpctl"):
        ok, target = _set_volume_wpctl(sink, volume)
        backend = "wpctl"
        if not ok and _has("amixer"):
            ok, target = _set_volume_alsa_output("", volume)
            backend = "alsa"
    elif _has("amixer"):
        ok, target = _set_volume_alsa_output(sink_token, volume)
        backend = "alsa"
    else:
        print(json.dumps({"ok": False, "error": "no pactl/wpctl/amixer available"}))
        return 3

    if not ok:
        print(json.dumps({"ok": False, "error": "volume set failed", "sink": target}))
        return 1

    print(json.dumps({"ok": True, "sink": target, "volume_percent": volume, "backend": backend}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
