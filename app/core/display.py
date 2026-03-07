from __future__ import annotations

import math
import re
import shutil
from pathlib import Path
from typing import Any

from app.core.systeminfo import run_cmd
from app.core.timeutil import utc_now

DISPLAY_MOUNT_ORIENTATIONS = (
    "landscape_cable_bottom",
    "landscape_cable_top",
    "portrait_cable_left",
    "portrait_cable_right",
    "unknown",
    "custom",
)

_MOUNT_TO_CONTENT: dict[str, tuple[str, int]] = {
    "landscape_cable_bottom": ("landscape", 0),
    "landscape_cable_top": ("landscape", 180),
    "portrait_cable_right": ("portrait", 90),
    "portrait_cable_left": ("portrait", 270),
}

_MANUFACTURER_CODES = {
    "AOC": "AOC",
    "APP": "Apple",
    "AUS": "ASUS",
    "BNQ": "BenQ",
    "DEL": "Dell",
    "EIZ": "EIZO",
    "GSM": "LG",
    "HPN": "HP",
    "HWP": "Huawei",
    "LEN": "Lenovo",
    "PHL": "Philips",
    "SAM": "Samsung",
    "SNY": "Sony",
    "VSC": "ViewSonic",
}


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception:
        return ""


def _safe_read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except Exception:
        return b""


def _connector_keys(name: str) -> set[str]:
    value = str(name or "").strip().upper()
    if not value:
        return set()
    keys = {re.sub(r"[^A-Z0-9]", "", value)}
    keys.add(re.sub(r"[^A-Z0-9]", "", value.replace("-A-", "-")))
    keys.add(re.sub(r"[^A-Z0-9]", "", value.replace("HDMI-A-", "HDMI-")))
    return {key for key in keys if key}


def _read_drm_connectors() -> list[dict[str, Any]]:
    root = Path("/sys/class/drm")
    if not root.exists():
        return []

    connectors: list[dict[str, Any]] = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        status_path = entry / "status"
        if not status_path.exists():
            continue
        # Example sysfs name: card0-HDMI-A-1 -> connector HDMI-A-1
        raw_name = entry.name
        connector = raw_name.split("-", 1)[1] if "-" in raw_name else raw_name
        status = _safe_read_text(status_path).lower()
        if status not in ("connected", "disconnected", "unknown"):
            status = "unknown"

        enabled_raw = _safe_read_text(entry / "enabled").lower()
        enabled = enabled_raw in ("enabled", "1", "yes", "true")

        modes_raw = _safe_read_text(entry / "modes")
        available_modes = [line.strip() for line in modes_raw.splitlines() if line.strip()]

        mode_raw = _safe_read_text(entry / "mode")
        current_mode = mode_raw.strip() or None

        connectors.append(
            {
                "connector": connector,
                "status": status,
                "enabled": enabled,
                "available_modes": available_modes,
                "current_mode": current_mode,
                "edid_path": str((entry / "edid").resolve()),
                "edid_raw": _safe_read_bytes(entry / "edid"),
            }
        )

    return connectors


def _decode_manufacturer_code(raw: int) -> str:
    if raw <= 0:
        return ""
    chars = [
        chr(((raw >> 10) & 0x1F) + 64),
        chr(((raw >> 5) & 0x1F) + 64),
        chr((raw & 0x1F) + 64),
    ]
    code = "".join(c if "A" <= c <= "Z" else "" for c in chars)
    return code.strip()


def _parse_descriptor_text(block: bytes) -> str:
    text = block[5:18].decode("ascii", errors="ignore")
    return text.replace("\n", "").replace("\r", "").strip()


def _parse_preferred_mode_from_dtd(block: bytes) -> tuple[str | None, float | None]:
    if len(block) != 18:
        return None, None
    pixel_clock_10khz = int.from_bytes(block[0:2], "little")
    if pixel_clock_10khz <= 0:
        return None, None
    h_active = block[2] + ((block[4] & 0xF0) << 4)
    h_blanking = block[3] + ((block[4] & 0x0F) << 8)
    v_active = block[5] + ((block[7] & 0xF0) << 4)
    v_blanking = block[6] + ((block[7] & 0x0F) << 8)
    if h_active <= 0 or v_active <= 0:
        return None, None
    total_h = h_active + h_blanking
    total_v = v_active + v_blanking
    refresh_hz = None
    if total_h > 0 and total_v > 0:
        pixel_clock_hz = pixel_clock_10khz * 10_000
        refresh_hz = round(pixel_clock_hz / float(total_h * total_v), 2)
    return f"{h_active}x{v_active}", refresh_hz


def _parse_edid(edid_raw: bytes) -> dict[str, Any]:
    if len(edid_raw) < 128:
        return {}

    base = edid_raw[:128]
    manufacturer_code = _decode_manufacturer_code(int.from_bytes(base[8:10], "big"))
    product_code = int.from_bytes(base[10:12], "little")
    serial_number = int.from_bytes(base[12:16], "little")
    width_cm = int(base[21] or 0)
    height_cm = int(base[22] or 0)
    width_mm = width_cm * 10 if width_cm > 0 else None
    height_mm = height_cm * 10 if height_cm > 0 else None

    monitor_name = ""
    serial_text = ""
    preferred_mode = None
    preferred_refresh_hz = None

    for idx in range(4):
        start = 54 + idx * 18
        block = base[start : start + 18]
        if len(block) < 18:
            continue
        if block[0] == 0 and block[1] == 0:
            descriptor_type = block[3]
            if descriptor_type == 0xFC:
                monitor_name = _parse_descriptor_text(block)
            elif descriptor_type == 0xFF:
                serial_text = _parse_descriptor_text(block)
            continue
        if preferred_mode is None:
            preferred_mode, preferred_refresh_hz = _parse_preferred_mode_from_dtd(block)

    if serial_number <= 0:
        serial = serial_text or ""
    else:
        serial = str(serial_number)

    manufacturer_name = _MANUFACTURER_CODES.get(manufacturer_code, "")
    model = monitor_name or (str(product_code) if product_code > 0 else "")

    audio_supported = False
    digital = bool(base[20] & 0x80)
    ext_count = int(base[126] or 0)
    # CTA extension can hint audio support (basic audio bit in CTA header byte 3 bit 6).
    if ext_count > 0 and len(edid_raw) >= 256:
        ext = edid_raw[128:256]
        if len(ext) >= 4 and ext[0] == 0x02:
            audio_supported = bool(ext[3] & 0x40)

    return {
        "manufacturer_code": manufacturer_code,
        "manufacturer_name": manufacturer_name,
        "model": model,
        "product_code": str(product_code) if product_code > 0 else "",
        "serial_number": serial,
        "display_name": monitor_name or " ".join(part for part in (manufacturer_name, model) if part).strip(),
        "physical_width_mm": width_mm,
        "physical_height_mm": height_mm,
        "preferred_mode": preferred_mode,
        "preferred_refresh_hz": preferred_refresh_hz,
        "audio_supported": audio_supported,
        "digital": digital,
    }


def _parse_xrandr() -> dict[str, dict[str, Any]]:
    binary = shutil.which("xrandr")
    if not binary:
        return {}
    rc, out, _ = run_cmd([binary, "--query"], timeout=6)
    if rc != 0 or not out:
        return {}

    result: dict[str, dict[str, Any]] = {}
    current_connector = ""
    for raw_line in out.splitlines():
        line = raw_line.rstrip("\n")
        if not line:
            continue
        if not line.startswith(" "):
            parts = line.split()
            if len(parts) < 2:
                current_connector = ""
                continue
            connector = parts[0].strip()
            state = parts[1].strip().lower()
            if state not in ("connected", "disconnected"):
                current_connector = ""
                continue
            current_connector = connector
            current_mode = None
            current_refresh_hz = None
            mode_match = re.search(r"(\d{3,5}x\d{3,5})\+\d+\+\d+", line)
            if mode_match:
                current_mode = mode_match.group(1)
            result[current_connector] = {
                "connector": connector,
                "status": state,
                "current_mode": current_mode,
                "current_refresh_hz": current_refresh_hz,
                "preferred_mode": None,
                "available_modes": {},
            }
            continue

        if not current_connector:
            continue

        stripped = line.strip()
        if not stripped:
            continue
        mode_name = stripped.split()[0]
        if "x" not in mode_name:
            continue

        rates: list[float] = []
        preferred = False
        current = False
        for token in stripped.split()[1:]:
            marker_current = "*" in token
            marker_preferred = "+" in token
            clean = token.replace("*", "").replace("+", "")
            try:
                rate = float(clean)
            except Exception:
                continue
            rates.append(rate)
            if marker_current:
                current = True
                result[current_connector]["current_refresh_hz"] = rate
            if marker_preferred:
                preferred = True
        if mode_name not in result[current_connector]["available_modes"]:
            result[current_connector]["available_modes"][mode_name] = []
        known_rates = result[current_connector]["available_modes"][mode_name]
        for rate in rates:
            if rate not in known_rates:
                known_rates.append(rate)
        if current:
            result[current_connector]["current_mode"] = mode_name
        if preferred and not result[current_connector]["preferred_mode"]:
            result[current_connector]["preferred_mode"] = mode_name

    return result


def normalize_mount_orientation(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    if raw in DISPLAY_MOUNT_ORIENTATIONS:
        return raw
    # Legacy compatibility if older values are still around.
    if raw == "horizontal":
        return "landscape_cable_bottom"
    if raw == "vertical":
        return "portrait_cable_right"
    return "unknown"


def _derive_orientation(mount_orientation: str, current_mode: str | None) -> tuple[str, int]:
    mapped = _MOUNT_TO_CONTENT.get(mount_orientation)
    if mapped:
        return mapped
    mode = str(current_mode or "").strip()
    if "x" in mode:
        try:
            w_raw, h_raw = mode.lower().split("x", 1)
            width = int(re.sub(r"[^0-9]", "", w_raw) or "0")
            height = int(re.sub(r"[^0-9]", "", h_raw) or "0")
            if width > 0 and height > 0 and height > width:
                return "portrait", 90
        except Exception:
            pass
    return "landscape", 0


def _display_config(cfg: dict) -> dict:
    root = cfg.get("display_config") if isinstance(cfg.get("display_config"), dict) else {}
    connectors = root.get("connectors") if isinstance(root.get("connectors"), dict) else {}
    root["connectors"] = connectors
    cfg["display_config"] = root
    return root


def update_display_config(
    cfg: dict,
    connector: str,
    mount_orientation: str | None = None,
    active: bool | None = None,
    friendly_name: str | None = None,
    note: str | None = None,
) -> dict:
    normalized_connector = str(connector or "").strip()
    if not normalized_connector:
        raise ValueError("connector is required")
    root = _display_config(cfg)
    connectors = root["connectors"]
    item = connectors.get(normalized_connector) if isinstance(connectors.get(normalized_connector), dict) else {}
    if mount_orientation is not None:
        item["mount_orientation"] = normalize_mount_orientation(mount_orientation)
    if active is not None:
        item["active"] = bool(active)
    if friendly_name is not None:
        item["friendly_name"] = str(friendly_name).strip()[:120]
    if note is not None:
        item["note"] = str(note).strip()[:240]
    item["updated_at"] = utc_now()
    connectors[normalized_connector] = item
    root["updated_at"] = utc_now()
    cfg["display_config"] = root
    cfg["updated_at"] = utc_now()
    return item


def get_display_snapshot(cfg: dict) -> dict[str, Any]:
    cfg = cfg if isinstance(cfg, dict) else {}
    now = utc_now()
    warnings: list[str] = []
    connectors = _read_drm_connectors()
    xrandr = _parse_xrandr()
    xrandr_by_key: dict[str, dict[str, Any]] = {}
    for _, xr in xrandr.items():
        for key in _connector_keys(str(xr.get("connector") or "")):
            xrandr_by_key[key] = xr

    display_cfg = _display_config(cfg)
    overrides = display_cfg.get("connectors") if isinstance(display_cfg.get("connectors"), dict) else {}

    displays: list[dict[str, Any]] = []
    for connector_info in connectors:
        connector = str(connector_info.get("connector") or "").strip()
        if not connector:
            continue
        xr = None
        for key in _connector_keys(connector):
            if key in xrandr_by_key:
                xr = xrandr_by_key[key]
                break
        override = overrides.get(connector) if isinstance(overrides.get(connector), dict) else {}

        edid_raw = connector_info.get("edid_raw") if isinstance(connector_info.get("edid_raw"), (bytes, bytearray)) else b""
        edid = _parse_edid(bytes(edid_raw)) if edid_raw else {}
        if connector_info.get("status") == "connected" and not edid:
            warnings.append(f"{connector}: connected but EDID unavailable or unreadable")

        available_modes = list(connector_info.get("available_modes") or [])
        xr_modes = []
        if isinstance(xr, dict):
            xr_modes = list((xr.get("available_modes") or {}).keys())
        for mode in xr_modes:
            if mode not in available_modes:
                available_modes.append(mode)

        current_mode = (
            (xr.get("current_mode") if isinstance(xr, dict) else None)
            or connector_info.get("current_mode")
            or edid.get("preferred_mode")
            or (available_modes[0] if available_modes else None)
        )
        preferred_mode = (
            (xr.get("preferred_mode") if isinstance(xr, dict) else None)
            or edid.get("preferred_mode")
            or (available_modes[0] if available_modes else None)
        )
        current_refresh_hz = xr.get("current_refresh_hz") if isinstance(xr, dict) else None
        if current_refresh_hz is not None:
            try:
                current_refresh_hz = float(current_refresh_hz)
            except Exception:
                current_refresh_hz = None

        supported_refresh_hz: list[float] = []
        if isinstance(xr, dict) and isinstance(xr.get("available_modes"), dict) and current_mode:
            raw_rates = xr["available_modes"].get(current_mode) or []
            for raw_rate in raw_rates:
                try:
                    rate = float(raw_rate)
                except Exception:
                    continue
                if rate not in supported_refresh_hz:
                    supported_refresh_hz.append(rate)

        mount_orientation = normalize_mount_orientation(override.get("mount_orientation"))
        content_orientation, rotation_degrees = _derive_orientation(mount_orientation, str(current_mode or ""))
        active = bool(override.get("active")) if "active" in override else bool(connector_info.get("status") == "connected")

        width_mm = edid.get("physical_width_mm")
        height_mm = edid.get("physical_height_mm")
        diagonal_inch = None
        aspect_ratio = None
        if isinstance(width_mm, int) and isinstance(height_mm, int) and width_mm > 0 and height_mm > 0:
            diagonal_inch = round(math.sqrt((width_mm ** 2) + (height_mm ** 2)) / 25.4, 1)
            gcd_val = math.gcd(width_mm, height_mm)
            if gcd_val > 0:
                aspect_ratio = f"{int(width_mm / gcd_val)}:{int(height_mm / gcd_val)}"

        model = str(edid.get("model") or "").strip()
        manufacturer_name = str(edid.get("manufacturer_name") or "").strip()
        display_name = str(
            override.get("friendly_name")
            or edid.get("display_name")
            or (" ".join(part for part in (manufacturer_name, model) if part).strip())
            or connector
        ).strip()

        displays.append(
            {
                "connector": connector,
                "status": str(connector_info.get("status") or "unknown"),
                "connected": bool(connector_info.get("status") == "connected"),
                "enabled": bool(connector_info.get("enabled", False)),
                "active": active,
                "manufacturer_code": str(edid.get("manufacturer_code") or ""),
                "manufacturer_name": manufacturer_name,
                "model": model,
                "product_code": str(edid.get("product_code") or ""),
                "serial_number": str(edid.get("serial_number") or ""),
                "display_name": display_name,
                "friendly_name": str(override.get("friendly_name") or ""),
                "note": str(override.get("note") or ""),
                "edid_available": bool(edid_raw),
                "physical_width_mm": width_mm if isinstance(width_mm, int) and width_mm > 0 else None,
                "physical_height_mm": height_mm if isinstance(height_mm, int) and height_mm > 0 else None,
                "diagonal_inch": diagonal_inch,
                "aspect_ratio": aspect_ratio,
                "preferred_mode": str(preferred_mode or "") or None,
                "current_mode": str(current_mode or "") or None,
                "current_refresh_hz": current_refresh_hz,
                "supported_refresh_hz": supported_refresh_hz,
                "available_modes": available_modes,
                "audio_supported": bool(edid.get("audio_supported", False)) if edid else None,
                "digital": bool(edid.get("digital", False)) if edid else None,
                "hdr_supported": None,
                "brightness_percent": None,
                "mount_orientation": mount_orientation,
                "content_orientation": content_orientation,
                "rotation_degrees": rotation_degrees,
                "edid_path": str(connector_info.get("edid_path") or ""),
                "last_detected_at": now,
            }
        )

    # If xrandr shows displays but sysfs is unavailable, include fallback entries.
    known_connectors = {str(item.get("connector") or "") for item in displays}
    for _, xr in xrandr.items():
        connector = str(xr.get("connector") or "").strip()
        if not connector or connector in known_connectors:
            continue
        mode = str(xr.get("current_mode") or "") or None
        mount_orientation = normalize_mount_orientation(((overrides.get(connector) or {}).get("mount_orientation")))
        content_orientation, rotation_degrees = _derive_orientation(mount_orientation, mode)
        displays.append(
            {
                "connector": connector,
                "status": str(xr.get("status") or "unknown"),
                "connected": bool(str(xr.get("status") or "").lower() == "connected"),
                "enabled": bool(str(xr.get("status") or "").lower() == "connected"),
                "active": bool((overrides.get(connector) or {}).get("active", True)),
                "manufacturer_code": "",
                "manufacturer_name": "",
                "model": "",
                "product_code": "",
                "serial_number": "",
                "display_name": connector,
                "friendly_name": str((overrides.get(connector) or {}).get("friendly_name") or ""),
                "note": str((overrides.get(connector) or {}).get("note") or ""),
                "edid_available": False,
                "physical_width_mm": None,
                "physical_height_mm": None,
                "diagonal_inch": None,
                "aspect_ratio": None,
                "preferred_mode": str(xr.get("preferred_mode") or "") or None,
                "current_mode": mode,
                "current_refresh_hz": xr.get("current_refresh_hz"),
                "supported_refresh_hz": [],
                "available_modes": list((xr.get("available_modes") or {}).keys()),
                "audio_supported": None,
                "digital": None,
                "hdr_supported": None,
                "brightness_percent": None,
                "mount_orientation": mount_orientation,
                "content_orientation": content_orientation,
                "rotation_degrees": rotation_degrees,
                "edid_path": "",
                "last_detected_at": now,
            }
        )

    displays.sort(key=lambda item: str(item.get("connector") or ""))
    connected = [item for item in displays if bool(item.get("connected"))]
    active_connected = [item for item in connected if bool(item.get("active"))]
    primary_display = (active_connected[0] if active_connected else (connected[0] if connected else (displays[0] if displays else None)))

    summary = {
        "total": len(displays),
        "connected": len(connected),
        "active": len([item for item in displays if bool(item.get("active"))]),
        "edid_available": len([item for item in displays if bool(item.get("edid_available"))]),
        "disconnected": len([item for item in displays if str(item.get("status") or "") == "disconnected"]),
    }

    return {
        "displays": displays,
        "primary_display": primary_display,
        "display_summary": summary,
        "warnings": warnings,
        "detected_at": now,
        "sources": {
            "drm_sysfs": True,
            "edid": True,
            "xrandr": bool(xrandr),
        },
    }
