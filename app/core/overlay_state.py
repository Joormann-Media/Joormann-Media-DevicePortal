from __future__ import annotations

import os
import uuid
from pathlib import Path

from app.core.jsonio import read_json, write_json
from app.core.paths import DATA_DIR, STORAGE_CONFIG_PATH
from app.core.timeutil import utc_now


PLAYER_SOURCE_PATH = Path(DATA_DIR) / "player-source.json"


def _default_state() -> dict:
    return {
        "updatedAt": utc_now(),
        "flashMessages": [],
        "tickers": [],
        "popups": [],
    }


def _norm_str(value, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _norm_bool(value, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _norm_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        num = int(value)
    except Exception:
        num = default
    return max(minimum, min(maximum, num))


def _norm_float(value, default: float, minimum: float, maximum: float) -> float:
    try:
        num = float(value)
    except Exception:
        num = default
    return max(minimum, min(maximum, num))


def _color(value, fallback: str) -> str:
    raw = _norm_str(value, fallback)
    if len(raw) == 7 and raw.startswith("#"):
        part = raw[1:]
        if all(ch in "0123456789abcdefABCDEF" for ch in part):
            return raw
    return fallback


def resolve_overlay_state_path() -> Path:
    explicit = os.getenv("OVERLAY_STATE_PATH", "").strip() or os.getenv("DEVICEPLAYER_OVERLAY_STATE_PATH", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()

    source = read_json(str(PLAYER_SOURCE_PATH), None)
    if isinstance(source, dict):
        manifest = source.get("manifest") if isinstance(source.get("manifest"), dict) else {}
        manifest_path = _norm_str(manifest.get("path") or source.get("manifest_path"), "")
        if manifest_path:
            path = Path(manifest_path).expanduser().resolve().parent / "overlay-state.json"
            return path

    storage_cfg = read_json(STORAGE_CONFIG_PATH, None)
    if isinstance(storage_cfg, dict):
        internal = storage_cfg.get("internal") if isinstance(storage_cfg.get("internal"), dict) else {}
        if bool(internal.get("allow_media_storage", True)):
            mount_path = _norm_str(internal.get("mount_path"), "")
            if mount_path:
                return (Path(mount_path).expanduser().resolve() / "stream/current/overlay-state.json")
        devices = storage_cfg.get("devices") if isinstance(storage_cfg.get("devices"), list) else []
        for row in devices:
            if not isinstance(row, dict):
                continue
            if not bool(row.get("allow_media_storage", False)):
                continue
            mount_path = _norm_str(row.get("mount_path"), "")
            if mount_path:
                return (Path(mount_path).expanduser().resolve() / "stream/current/overlay-state.json")

    return (Path(DATA_DIR) / "overlay-state.json").resolve()


def read_overlay_state() -> tuple[dict, Path]:
    path = resolve_overlay_state_path()
    raw = read_json(str(path), None)
    if not isinstance(raw, dict):
        return _default_state(), path

    state = _default_state()
    state["updatedAt"] = _norm_str(raw.get("updatedAt"), "") or utc_now()

    for key in ("flashMessages", "tickers", "popups"):
        rows = raw.get(key)
        if isinstance(rows, list):
            state[key] = [r for r in rows if isinstance(r, dict)]

    return state, path


def write_overlay_state(state: dict) -> tuple[bool, str, Path]:
    path = resolve_overlay_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = utc_now()
    ok, err = write_json(str(path), state, mode=0o600)
    return ok, err, path


def sanitize_flash(payload: dict) -> dict:
    return {
        "id": _norm_str(payload.get("id"), ""),
        "enabled": _norm_bool(payload.get("enabled"), True),
        "title": _norm_str(payload.get("title"), ""),
        "message": _norm_str(payload.get("message"), ""),
        "durationMs": _norm_int(payload.get("durationMs"), 5000, 500, 120000),
        "position": _norm_str(payload.get("position"), "top") if _norm_str(payload.get("position"), "top") in {"top", "center", "bottom"} else "top",
        "rotation": _norm_int(payload.get("rotation"), 0, -360, 360),
        "backgroundColor": _color(payload.get("backgroundColor"), "#111111"),
        "textColor": _color(payload.get("textColor"), "#ffffff"),
        "accentColor": _color(payload.get("accentColor"), "#0d6efd"),
        "fontSize": _norm_int(payload.get("fontSize"), 32, 12, 140),
        "padding": _norm_int(payload.get("padding"), 24, 0, 240),
        "opacity": _norm_float(payload.get("opacity"), 0.95, 0.05, 1.0),
    }


def sanitize_ticker(payload: dict) -> dict:
    return {
        "id": _norm_str(payload.get("id"), ""),
        "enabled": _norm_bool(payload.get("enabled"), True),
        "text": _norm_str(payload.get("text"), ""),
        "position": _norm_str(payload.get("position"), "bottom") if _norm_str(payload.get("position"), "bottom") in {"top", "bottom"} else "bottom",
        "rotation": _norm_int(payload.get("rotation"), 0, -360, 360),
        "speedPxPerSecond": _norm_float(payload.get("speedPxPerSecond"), 120.0, 10.0, 800.0),
        "height": _norm_int(payload.get("height"), 72, 24, 320),
        "paddingX": _norm_int(payload.get("paddingX"), 24, 0, 240),
        "backgroundColor": _color(payload.get("backgroundColor"), "#000000"),
        "textColor": _color(payload.get("textColor"), "#ffffff"),
        "fontSize": _norm_int(payload.get("fontSize"), 34, 12, 160),
        "opacity": _norm_float(payload.get("opacity"), 0.9, 0.05, 1.0),
    }


def sanitize_popup(payload: dict) -> dict:
    position = _norm_str(payload.get("position"), "center").lower()
    if position not in {"center", "top", "bottom", "top-left", "top-right", "bottom-left", "bottom-right"}:
        position = "center"
    trigger_mode = _norm_str(payload.get("triggerMode"), _norm_str(payload.get("trigger_mode"), "always")).lower()
    if trigger_mode not in {"always", "interval", "cron", "event"}:
        trigger_mode = "always"
    interval_unit = _norm_str(payload.get("intervalUnit"), _norm_str(payload.get("interval_unit"), "")).lower()
    if interval_unit not in {"", "minutes", "hours", "days"}:
        interval_unit = "minutes"

    return {
        "id": _norm_str(payload.get("id"), ""),
        "enabled": _norm_bool(payload.get("enabled"), True),
        "title": _norm_str(payload.get("title"), ""),
        "message": _norm_str(payload.get("message"), ""),
        "popupId": _norm_str(payload.get("popupId"), _norm_str(payload.get("popup_id"), "")),
        "popupSlug": _norm_str(payload.get("popupSlug"), _norm_str(payload.get("popup_slug"), "")),
        "popupName": _norm_str(payload.get("popupName"), _norm_str(payload.get("popup_name"), "")),
        "popupContent": _norm_str(payload.get("popupContent"), _norm_str(payload.get("popup_content"), "")),
        "durationMs": _norm_int(payload.get("durationMs"), 8000, 500, 120000),
        "position": position,
        "rotation": _norm_int(payload.get("rotation"), 0, -360, 360),
        "preRotated": _norm_bool(payload.get("preRotated"), _norm_bool(payload.get("pre_rotated"), False)),
        "imagePath": _norm_str(payload.get("imagePath"), ""),
        "imageUrl": _norm_str(payload.get("imageUrl"), _norm_str(payload.get("image_url"), "")),
        "imageData": _norm_str(payload.get("imageData"), _norm_str(payload.get("image_data"), "")),
        "imageMimeType": _norm_str(payload.get("imageMimeType"), _norm_str(payload.get("image_mime_type"), "")),
        "triggerMode": trigger_mode,
        "intervalValue": _norm_int(payload.get("intervalValue"), 0, 0, 10080),
        "intervalUnit": interval_unit,
        "scheduleCron": _norm_str(payload.get("scheduleCron"), _norm_str(payload.get("schedule_cron"), "")),
        "eventKey": _norm_str(payload.get("eventKey"), _norm_str(payload.get("event_key"), "")),
        "backgroundColor": _color(payload.get("backgroundColor"), "#ffffff"),
        "textColor": _color(payload.get("textColor"), "#111111"),
        "accentColor": _color(payload.get("accentColor"), "#dc3545"),
        "width": _norm_int(payload.get("width"), 800, 180, 3840),
        "height": _norm_int(payload.get("height"), 420, 120, 2160),
        "padding": _norm_int(payload.get("padding"), 24, 0, 240),
        "opacity": _norm_float(payload.get("opacity"), 1.0, 0.05, 1.0),
    }


def upsert_category_item(category_key: str, item: dict) -> tuple[dict, Path]:
    state, path = read_overlay_state()
    rows = state.get(category_key)
    if not isinstance(rows, list):
        rows = []
        state[category_key] = rows

    item_id = _norm_str(item.get("id"), "")
    if item_id == "":
        base = category_key.rstrip("s")
        if base.endswith("e"):
            base = base[:-1]
        item_id = f"{base}-{uuid.uuid4().hex[:10]}"
        item["id"] = item_id

    replaced = False
    for idx, row in enumerate(rows):
        if isinstance(row, dict) and _norm_str(row.get("id"), "") == item_id:
            rows[idx] = item
            replaced = True
            break
    if not replaced:
        rows.append(item)

    return state, path


def clear_category(category_key: str) -> tuple[bool, str, Path, dict]:
    state, _ = read_overlay_state()
    state[category_key] = []
    ok, err, path = write_overlay_state(state)
    return ok, err, path, state


def reset_overlay_state() -> tuple[bool, str, Path, dict]:
    state = _default_state()
    ok, err, path = write_overlay_state(state)
    return ok, err, path, state
