from __future__ import annotations

import re
from typing import Any


def storage_device_id(uuid: str, part_uuid: str) -> str:
    u = (uuid or "").strip()
    p = (part_uuid or "").strip()
    if u:
        return f"uuid:{u.lower()}"
    if p:
        return f"partuuid:{p.lower()}"
    return ""


def match_score(config_item: dict[str, Any], discovered_item: dict[str, Any]) -> int:
    cfg_uuid = str(config_item.get("uuid") or "").strip().lower()
    cfg_part = str(config_item.get("part_uuid") or "").strip().lower()
    disc_uuid = str(discovered_item.get("uuid") or "").strip().lower()
    disc_part = str(discovered_item.get("part_uuid") or "").strip().lower()
    if cfg_uuid and disc_uuid and cfg_uuid == disc_uuid:
        return 100
    if cfg_part and disc_part and cfg_part == disc_part:
        return 80
    return 0


def find_best_match(config_item: dict[str, Any], discovered_devices: list[dict[str, Any]]) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_score = 0
    for item in discovered_devices:
        score = match_score(config_item, item)
        if score > best_score:
            best = item
            best_score = score
    return best


def safe_mount_slug(*parts: str) -> str:
    base = "-".join([p for p in parts if p]).strip().lower()
    base = re.sub(r"[^a-z0-9._-]+", "-", base)
    base = re.sub(r"-{2,}", "-", base).strip("-._")
    return base or "storage"
