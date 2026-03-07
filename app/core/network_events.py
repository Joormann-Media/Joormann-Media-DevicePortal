from __future__ import annotations

import json
import os
import tempfile
from typing import Any

from app.core.paths import ASSET_DIR
from app.core.timeutil import utc_now

_RESOLVED_ASSET_DIR: str | None = None


def _resolve_asset_dir() -> str | None:
    global _RESOLVED_ASSET_DIR
    if _RESOLVED_ASSET_DIR:
        return _RESOLVED_ASSET_DIR

    candidates = [
        ASSET_DIR,
        "/tmp/deviceportal/assets",
    ]
    for candidate in candidates:
        try:
            os.makedirs(candidate, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=candidate, prefix=".write-test-", delete=True):
                pass
            _RESOLVED_ASSET_DIR = candidate
            return _RESOLVED_ASSET_DIR
        except Exception:
            continue
    return None


def _events_log_path() -> str | None:
    asset_dir = _resolve_asset_dir()
    if not asset_dir:
        return None
    return os.path.join(asset_dir, "network-events.log")


def _wps_state_path() -> str | None:
    asset_dir = _resolve_asset_dir()
    if not asset_dir:
        return None
    return os.path.join(asset_dir, "wps-state.json")


def _bt_pairing_state_path() -> str | None:
    asset_dir = _resolve_asset_dir()
    if not asset_dir:
        return None
    return os.path.join(asset_dir, "bt-pairing-state.json")


def log_event(kind: str, message: str, level: str = "info", data: dict[str, Any] | None = None) -> None:
    path = _events_log_path()
    if not path:
        return
    payload: dict[str, Any] = {
        "ts": utc_now(),
        "kind": str(kind or "network"),
        "level": str(level or "info"),
        "message": str(message or ""),
    }
    if data:
        payload["data"] = data
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        return


def read_events(limit: int = 120) -> list[dict[str, Any]]:
    path = _events_log_path()
    if not path:
        return []
    if limit <= 0:
        return []
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
    except Exception:
        return []
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
    path = _wps_state_path()
    if not path:
        return
    payload = dict(state or {})
    payload["updated_at"] = utc_now()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        return


def get_wps_state() -> dict[str, Any]:
    path = _wps_state_path()
    if not path:
        return {}
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def set_bt_pairing_state(state: dict[str, Any]) -> None:
    path = _bt_pairing_state_path()
    if not path:
        return
    payload = dict(state or {})
    payload["updated_at"] = utc_now()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        return


def get_bt_pairing_state() -> dict[str, Any]:
    path = _bt_pairing_state_path()
    if not path:
        return {}
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
