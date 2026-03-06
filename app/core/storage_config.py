from __future__ import annotations

from typing import Any

from app.core.jsonio import read_json, write_json
from app.core.paths import STORAGE_CONFIG_PATH
from app.core.timeutil import utc_now

INTERNAL_DEFAULT: dict[str, Any] = {
    "id": "internal-media",
    "name": "Interner Medienspeicher",
    "type": "internal_loop",
    "enabled": True,
    "image_path": "/var/lib/deviceportal/media.img",
    "mount_path": "/mnt/deviceportal/media",
    "filesystem": "ext4",
    "size_gb": 20,
    "auto_mount": True,
    "allow_portal_storage": True,
    "allow_media_storage": True,
    "last_error": "",
}

DEFAULT_STORAGE_CONFIG: dict[str, Any] = {
    "version": 1,
    "internal": dict(INTERNAL_DEFAULT),
    "devices": [],
    "ignored": [],
}


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _normalize_internal(item: Any) -> dict[str, Any]:
    src = item if isinstance(item, dict) else {}
    out = dict(INTERNAL_DEFAULT)
    out.update({k: v for k, v in src.items() if v is not None})
    out["id"] = str(out.get("id") or INTERNAL_DEFAULT["id"]).strip() or INTERNAL_DEFAULT["id"]
    out["name"] = str(out.get("name") or INTERNAL_DEFAULT["name"]).strip() or INTERNAL_DEFAULT["name"]
    out["type"] = "internal_loop"
    out["image_path"] = str(out.get("image_path") or INTERNAL_DEFAULT["image_path"]).strip() or INTERNAL_DEFAULT["image_path"]
    out["mount_path"] = str(out.get("mount_path") or INTERNAL_DEFAULT["mount_path"]).strip() or INTERNAL_DEFAULT["mount_path"]
    out["filesystem"] = str(out.get("filesystem") or INTERNAL_DEFAULT["filesystem"]).strip() or INTERNAL_DEFAULT["filesystem"]
    try:
        out["size_gb"] = max(1, int(out.get("size_gb") or INTERNAL_DEFAULT["size_gb"]))
    except Exception:
        out["size_gb"] = INTERNAL_DEFAULT["size_gb"]
    out["enabled"] = _as_bool(out.get("enabled"), True)
    out["auto_mount"] = _as_bool(out.get("auto_mount"), True)
    out["allow_portal_storage"] = _as_bool(out.get("allow_portal_storage"), True)
    out["allow_media_storage"] = _as_bool(out.get("allow_media_storage"), True)
    out["last_error"] = str(out.get("last_error") or "").strip()
    return out


def _normalize_device(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    uuid = str(item.get("uuid") or "").strip()
    part_uuid = str(item.get("part_uuid") or "").strip()
    if not uuid and not part_uuid:
        return None
    return {
        "id": str(item.get("id") or "").strip(),
        "name": str(item.get("name") or "").strip(),
        "uuid": uuid,
        "part_uuid": part_uuid,
        "label": str(item.get("label") or "").strip(),
        "filesystem": str(item.get("filesystem") or "").strip(),
        "size_bytes": int(item.get("size_bytes") or 0),
        "vendor": str(item.get("vendor") or "").strip(),
        "model": str(item.get("model") or "").strip(),
        "serial": str(item.get("serial") or "").strip(),
        "transport": str(item.get("transport") or "").strip(),
        "mount_path": str(item.get("mount_path") or "").strip(),
        "mount_strategy": str(item.get("mount_strategy") or "persistent").strip() or "persistent",
        "mount_options": str(item.get("mount_options") or "defaults,noatime,nofail").strip() or "defaults,noatime,nofail",
        "is_enabled": _as_bool(item.get("is_enabled"), True),
        "auto_mount": _as_bool(item.get("auto_mount"), True),
        "allow_portal_storage": _as_bool(item.get("allow_portal_storage"), False),
        "allow_media_storage": _as_bool(item.get("allow_media_storage"), True),
        "added_at": str(item.get("added_at") or "").strip(),
        "last_seen_at": str(item.get("last_seen_at") or "").strip(),
        "last_seen_device_path": str(item.get("last_seen_device_path") or "").strip(),
        "last_known_present": _as_bool(item.get("last_known_present"), False),
        "last_error": str(item.get("last_error") or "").strip(),
        "notes": str(item.get("notes") or "").strip(),
    }


def ensure_storage_config() -> dict[str, Any]:
    cfg = read_json(STORAGE_CONFIG_PATH, None)
    if not isinstance(cfg, dict):
        cfg = {}

    changed = False
    for key, value in DEFAULT_STORAGE_CONFIG.items():
        if key not in cfg:
            cfg[key] = value
            changed = True

    normalized_internal = _normalize_internal(cfg.get("internal"))
    if cfg.get("internal") != normalized_internal:
        cfg["internal"] = normalized_internal
        changed = True

    devices_raw = cfg.get("devices")
    if not isinstance(devices_raw, list):
        devices_raw = []
        cfg["devices"] = devices_raw
        changed = True
    normalized_devices: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in devices_raw:
        norm = _normalize_device(item)
        if not norm:
            continue
        dev_id = norm["id"]
        if not dev_id:
            continue
        if dev_id in seen_ids:
            continue
        seen_ids.add(dev_id)
        normalized_devices.append(norm)
    if normalized_devices != devices_raw:
        cfg["devices"] = normalized_devices
        changed = True

    ignored_raw = cfg.get("ignored")
    if not isinstance(ignored_raw, list):
        ignored_raw = []
        changed = True
    ignored: list[str] = []
    for item in ignored_raw:
        text = str(item or "").strip()
        if text and text not in ignored:
            ignored.append(text)
    if ignored != ignored_raw:
        cfg["ignored"] = ignored
        changed = True

    if "created_at" not in cfg:
        cfg["created_at"] = utc_now()
        changed = True
    if changed:
        cfg["updated_at"] = utc_now()
        write_json(STORAGE_CONFIG_PATH, cfg, mode=0o600)
    return cfg


def save_storage_config(cfg: dict[str, Any]) -> tuple[bool, str]:
    cfg["updated_at"] = utc_now()
    return write_json(STORAGE_CONFIG_PATH, cfg, mode=0o600)
