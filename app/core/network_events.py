from __future__ import annotations

import json
import os
from typing import Any

from app.core.paths import ASSET_DIR
from app.core.timeutil import utc_now

EVENTS_LOG_PATH = os.path.join(ASSET_DIR, "network-events.log")
WPS_STATE_PATH = os.path.join(ASSET_DIR, "wps-state.json")


def _ensure_dir() -> None:
    os.makedirs(ASSET_DIR, exist_ok=True)


def log_event(kind: str, message: str, level: str = "info", data: dict[str, Any] | None = None) -> None:
    _ensure_dir()
    payload: dict[str, Any] = {
        "ts": utc_now(),
        "kind": str(kind or "network"),
        "level": str(level or "info"),
        "message": str(message or ""),
    }
    if data:
        payload["data"] = data
    with open(EVENTS_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def read_events(limit: int = 120) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    if not os.path.exists(EVENTS_LOG_PATH):
        return []
    with open(EVENTS_LOG_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()[-limit:]
    events: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events


def set_wps_state(state: dict[str, Any]) -> None:
    _ensure_dir()
    payload = dict(state or {})
    payload["updated_at"] = utc_now()
    with open(WPS_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def get_wps_state() -> dict[str, Any]:
    if not os.path.exists(WPS_STATE_PATH):
        return {}
    try:
        with open(WPS_STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
