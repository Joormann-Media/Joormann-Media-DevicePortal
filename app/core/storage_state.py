from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from app.core.netcontrol import NetControlError, storage_mount, storage_probe, storage_unmount
from app.core.network_events import log_event
from app.core.storage_config import ensure_storage_config, save_storage_config
from app.core.storage_identity import find_best_match, safe_mount_slug, storage_device_id
from app.core.timeutil import utc_now

MOUNT_BASE_PATH = Path("/mnt/deviceportal/storage")


def _ensure_mount_base() -> None:
    MOUNT_BASE_PATH.mkdir(parents=True, exist_ok=True)


def _read_probe_devices() -> list[dict[str, Any]]:
    payload = storage_probe()
    devices = payload.get("devices")
    if not isinstance(devices, list):
        return []
    clean: list[dict[str, Any]] = []
    for item in devices:
        if not isinstance(item, dict):
            continue
        uuid = str(item.get("uuid") or "").strip()
        part_uuid = str(item.get("part_uuid") or "").strip()
        if not uuid and not part_uuid:
            continue
        norm = {
            "uuid": uuid,
            "part_uuid": part_uuid,
            "label": str(item.get("label") or "").strip(),
            "filesystem": str(item.get("filesystem") or "").strip(),
            "size_bytes": int(item.get("size_bytes") or 0),
            "vendor": str(item.get("vendor") or "").strip(),
            "model": str(item.get("model") or "").strip(),
            "serial": str(item.get("serial") or "").strip(),
            "transport": str(item.get("transport") or "").strip(),
            "device_path": str(item.get("device_path") or "").strip(),
            "mount_path": str(item.get("mount_path") or "").strip(),
            "mounted": bool(item.get("mounted", False)),
            "hotplug": bool(item.get("hotplug", False)),
            "removable": bool(item.get("removable", False)),
        }
        norm["id"] = storage_device_id(norm["uuid"], norm["part_uuid"])
        if not norm["id"]:
            continue
        clean.append(norm)
    return clean


def _next_mount_path(cfg_devices: list[dict[str, Any]], discovered: dict[str, Any]) -> str:
    _ensure_mount_base()
    label = str(discovered.get("label") or "").strip()
    uuid = str(discovered.get("uuid") or "").strip()
    part_uuid = str(discovered.get("part_uuid") or "").strip()
    source = label or uuid or part_uuid or "storage"
    slug = safe_mount_slug(source)
    existing_paths = {str(item.get("mount_path") or "").strip() for item in cfg_devices if isinstance(item, dict)}
    candidate = MOUNT_BASE_PATH / slug
    idx = 2
    while str(candidate) in existing_paths:
        candidate = MOUNT_BASE_PATH / f"{slug}-{idx}"
        idx += 1
    return str(candidate)


def _mounted_source(target: str) -> str:
    if not target:
        return ""
    try:
        out = subprocess.check_output(["findmnt", "-rn", "-M", target, "-o", "SOURCE"], text=True, stderr=subprocess.DEVNULL)
        return out.strip()
    except Exception:
        return ""


def _mounted_fstype(target: str) -> str:
    if not target:
        return ""
    try:
        out = subprocess.check_output(["findmnt", "-rn", "-M", target, "-o", "FSTYPE"], text=True, stderr=subprocess.DEVNULL)
        return out.strip()
    except Exception:
        return ""


def _file_fstype(path: str) -> str:
    if not path:
        return ""
    try:
        out = subprocess.check_output(["blkid", "-o", "value", "-s", "TYPE", path], text=True, stderr=subprocess.DEVNULL)
        return out.strip()
    except Exception:
        return ""


def _disk_usage(path: str) -> tuple[int, int, int, int]:
    if not path:
        return 0, 0, 0, 0
    try:
        u = shutil.disk_usage(path)
        total = int(u.total)
        used = int(u.used)
        free = int(u.free)
        percent = int(round((used / total) * 100)) if total > 0 else 0
        return total, used, free, percent
    except Exception:
        return 0, 0, 0, 0


def _cfg_device_summary(cfg_item: dict[str, Any], present: bool, mounted: bool, discovered_item: dict[str, Any] | None) -> dict[str, Any]:
    current_mount_path = str((discovered_item or {}).get("mount_path") or "").strip()
    usage_path = current_mount_path if mounted and current_mount_path else (cfg_item.get("mount_path") or "")
    total_b, used_b, free_b, used_pct = _disk_usage(str(usage_path)) if mounted else (int((discovered_item or {}).get("size_bytes") or cfg_item.get("size_bytes") or 0), 0, 0, 0)
    drive_name = cfg_item.get("name") or (discovered_item or {}).get("label") or cfg_item.get("label") or cfg_item.get("uuid") or cfg_item.get("part_uuid") or cfg_item.get("id")
    return {
        "id": cfg_item.get("id", ""),
        "drive_name": drive_name,
        "drive_type": "usb" if ((discovered_item or {}).get("transport") == "usb" or (discovered_item or {}).get("removable")) else "external",
        "name": cfg_item.get("name", ""),
        "uuid": cfg_item.get("uuid", ""),
        "part_uuid": cfg_item.get("part_uuid", ""),
        "label": (discovered_item or {}).get("label") or cfg_item.get("label", ""),
        "filesystem": (discovered_item or {}).get("filesystem") or cfg_item.get("filesystem", ""),
        "size_bytes": int((discovered_item or {}).get("size_bytes") or cfg_item.get("size_bytes") or 0),
        "total_bytes": total_b,
        "used_bytes": used_b,
        "free_bytes": free_b,
        "used_percent": used_pct,
        "vendor": (discovered_item or {}).get("vendor") or cfg_item.get("vendor", ""),
        "model": (discovered_item or {}).get("model") or cfg_item.get("model", ""),
        "serial": (discovered_item or {}).get("serial") or cfg_item.get("serial", ""),
        "transport": (discovered_item or {}).get("transport") or cfg_item.get("transport", ""),
        "mount_path": cfg_item.get("mount_path", ""),
        "mount_strategy": cfg_item.get("mount_strategy", "persistent"),
        "mount_options": cfg_item.get("mount_options", "defaults,noatime,nofail"),
        "is_enabled": bool(cfg_item.get("is_enabled", True)),
        "auto_mount": bool(cfg_item.get("auto_mount", True)),
        "allow_portal_storage": bool(cfg_item.get("allow_portal_storage", False)),
        "allow_media_storage": bool(cfg_item.get("allow_media_storage", True)),
        "added_at": cfg_item.get("added_at", ""),
        "last_seen_at": cfg_item.get("last_seen_at", ""),
        "last_seen_device_path": cfg_item.get("last_seen_device_path", ""),
        "last_known_present": bool(cfg_item.get("last_known_present", False)),
        "last_error": cfg_item.get("last_error", ""),
        "notes": cfg_item.get("notes", ""),
        "present": present,
        "mounted": mounted,
        "current_mount_path": current_mount_path,
        "state": "mounted" if mounted else ("present" if present else "missing"),
    }


def _internal_summary(cfg: dict[str, Any]) -> dict[str, Any]:
    internal = cfg.get("internal") if isinstance(cfg.get("internal"), dict) else {}
    image_path = str(internal.get("image_path") or "/var/lib/deviceportal/media.img")
    mount_path = str(internal.get("mount_path") or "/mnt/deviceportal/media")
    expected_fs = str(internal.get("filesystem") or "ext4")
    size_gb = int(internal.get("size_gb") or 20)

    image_exists = os.path.exists(image_path)
    image_size_bytes = os.path.getsize(image_path) if image_exists else 0
    mounted_source = _mounted_source(mount_path)
    mounted = bool(mounted_source)
    mounted_fstype = _mounted_fstype(mount_path) if mounted else ""
    file_fstype = _file_fstype(image_path) if image_exists else ""
    filesystem = mounted_fstype or file_fstype or expected_fs

    total_b, used_b, free_b, used_pct = _disk_usage(mount_path) if mounted else (image_size_bytes or size_gb * 1024 * 1024 * 1024, 0, 0, 0)
    present = image_exists
    state = "mounted" if mounted else ("present" if present else "missing")

    return {
        "id": str(internal.get("id") or "internal-media"),
        "drive_name": str(internal.get("name") or "Interner Medienspeicher"),
        "drive_type": "internal_loop",
        "type": "internal_loop",
        "enabled": bool(internal.get("enabled", True)),
        "auto_mount": bool(internal.get("auto_mount", True)),
        "allow_portal_storage": bool(internal.get("allow_portal_storage", True)),
        "allow_media_storage": bool(internal.get("allow_media_storage", True)),
        "image_path": image_path,
        "mount_path": mount_path,
        "filesystem": filesystem,
        "expected_filesystem": expected_fs,
        "size_gb": size_gb,
        "image_exists": image_exists,
        "image_size_bytes": image_size_bytes,
        "present": present,
        "mounted": mounted,
        "mounted_source": mounted_source,
        "state": state,
        "total_bytes": int(total_b),
        "used_bytes": int(used_b),
        "free_bytes": int(free_b),
        "used_percent": int(used_pct),
        "last_error": str(internal.get("last_error") or ""),
    }


def get_storage_state() -> dict[str, Any]:
    cfg = ensure_storage_config()
    cfg_devices = cfg.get("devices") if isinstance(cfg.get("devices"), list) else []
    ignored = set(cfg.get("ignored") if isinstance(cfg.get("ignored"), list) else [])
    discovered = _read_probe_devices()
    internal = _internal_summary(cfg)

    changed = False
    known: list[dict[str, Any]] = []
    matched_ids: set[str] = set()

    for cfg_item in cfg_devices:
        if not isinstance(cfg_item, dict):
            continue
        if not cfg_item.get("id"):
            cfg_item["id"] = storage_device_id(str(cfg_item.get("uuid") or ""), str(cfg_item.get("part_uuid") or ""))
            changed = True
        disc = find_best_match(cfg_item, discovered)
        present = disc is not None
        mounted = bool((disc or {}).get("mounted", False))
        now_ts = utc_now()
        if present:
            matched_ids.add(str((disc or {}).get("id") or ""))
            cfg_item["last_seen_at"] = now_ts
            cfg_item["last_known_present"] = True
            cfg_item["last_seen_device_path"] = str((disc or {}).get("device_path") or "")
            for key in ("label", "filesystem", "vendor", "model", "serial", "transport"):
                val = str((disc or {}).get(key) or "").strip()
                if val:
                    cfg_item[key] = val
            cfg_item["size_bytes"] = int((disc or {}).get("size_bytes") or cfg_item.get("size_bytes") or 0)
            changed = True
        else:
            if cfg_item.get("last_known_present", True):
                log_event("storage", "Storage device is now missing", level="warning", data={"id": cfg_item.get("id", ""), "mount_path": cfg_item.get("mount_path", "")})
                cfg_item["last_known_present"] = False
                changed = True

        if present and not mounted and bool(cfg_item.get("is_enabled", True)) and bool(cfg_item.get("auto_mount", True)):
            selector_type = "uuid" if str(cfg_item.get("uuid") or "").strip() else "partuuid"
            selector_value = str(cfg_item.get("uuid") if selector_type == "uuid" else cfg_item.get("part_uuid") or "").strip()
            mount_path = str(cfg_item.get("mount_path") or "").strip()
            if selector_value and mount_path:
                try:
                    storage_mount(
                        selector_type=selector_type,
                        selector_value=selector_value,
                        mount_path=mount_path,
                        mount_options=str(cfg_item.get("mount_options") or "defaults,noatime,nofail"),
                    )
                    cfg_item["last_error"] = ""
                    mounted = True
                    log_event("storage", "Storage device mounted automatically", data={"id": cfg_item.get("id", ""), "mount_path": mount_path})
                    changed = True
                except NetControlError as exc:
                    cfg_item["last_error"] = exc.detail or exc.message
                    changed = True

        known.append(_cfg_device_summary(cfg_item, present=present, mounted=mounted, discovered_item=disc))

    new_devices: list[dict[str, Any]] = []
    for disc in discovered:
        disc_id = str(disc.get("id") or "")
        if not disc_id or disc_id in matched_ids or disc_id in ignored:
            continue
        total = int(disc.get("size_bytes") or 0)
        drive_name = disc.get("label") or disc.get("uuid") or disc.get("part_uuid") or disc.get("device_path") or disc_id
        new_devices.append(
            {
                "id": disc_id,
                "drive_name": drive_name,
                "drive_type": "usb" if (disc.get("transport") == "usb" or disc.get("removable")) else "external",
                "uuid": disc.get("uuid", ""),
                "part_uuid": disc.get("part_uuid", ""),
                "label": disc.get("label", ""),
                "filesystem": disc.get("filesystem", ""),
                "size_bytes": total,
                "total_bytes": total,
                "used_bytes": 0,
                "free_bytes": 0,
                "used_percent": 0,
                "vendor": disc.get("vendor", ""),
                "model": disc.get("model", ""),
                "serial": disc.get("serial", ""),
                "transport": disc.get("transport", ""),
                "device_path": disc.get("device_path", ""),
                "mounted": bool(disc.get("mounted", False)),
                "mount_path": disc.get("mount_path", ""),
                "state": "new",
            }
        )

    ignored_devices: list[dict[str, Any]] = []
    for ignored_id in sorted(ignored):
        disc = next((item for item in discovered if str(item.get("id") or "") == ignored_id), None)
        total = int((disc or {}).get("size_bytes") or 0)
        ignored_devices.append(
            {
                "id": ignored_id,
                "drive_name": (disc or {}).get("label") or (disc or {}).get("uuid") or (disc or {}).get("part_uuid") or ignored_id,
                "drive_type": "usb" if ((disc or {}).get("transport") == "usb" or (disc or {}).get("removable")) else "external",
                "present": disc is not None,
                "uuid": (disc or {}).get("uuid", ""),
                "part_uuid": (disc or {}).get("part_uuid", ""),
                "label": (disc or {}).get("label", ""),
                "filesystem": (disc or {}).get("filesystem", ""),
                "size_bytes": total,
                "total_bytes": total,
                "used_bytes": 0,
                "free_bytes": 0,
                "used_percent": 0,
                "device_path": (disc or {}).get("device_path", ""),
            }
        )

    if changed:
        ok, err = save_storage_config(cfg)
        if not ok:
            raise NetControlError(code="storage_config_write_failed", message="Storage state changed but could not write config", detail=err)

    drives: list[dict[str, Any]] = [
        {
            "id": internal["id"],
            "drive_name": internal["drive_name"],
            "drive_type": internal["drive_type"],
            "filesystem": internal.get("filesystem", ""),
            "total_bytes": internal.get("total_bytes", 0),
            "used_bytes": internal.get("used_bytes", 0),
            "free_bytes": internal.get("free_bytes", 0),
            "used_percent": internal.get("used_percent", 0),
            "mount_path": internal.get("mount_path", ""),
            "state": internal.get("state", "missing"),
            "present": internal.get("present", False),
            "mounted": internal.get("mounted", False),
            "uuid": "",
            "part_uuid": "",
            "is_internal": True,
        }
    ]
    for item in known:
        drives.append(
            {
                "id": item.get("id", ""),
                "drive_name": item.get("drive_name", ""),
                "drive_type": item.get("drive_type", "external"),
                "filesystem": item.get("filesystem", ""),
                "total_bytes": item.get("total_bytes", item.get("size_bytes", 0)),
                "used_bytes": item.get("used_bytes", 0),
                "free_bytes": item.get("free_bytes", 0),
                "used_percent": item.get("used_percent", 0),
                "mount_path": item.get("mount_path", ""),
                "state": item.get("state", "missing"),
                "present": item.get("present", False),
                "mounted": item.get("mounted", False),
                "uuid": item.get("uuid", ""),
                "part_uuid": item.get("part_uuid", ""),
                "is_internal": False,
            }
        )

    return {
        "internal": internal,
        "detected_count": len(discovered),
        "known_count": len(known),
        "new_count": len(new_devices),
        "known": known,
        "new": new_devices,
        "ignored_count": len(ignored),
        "ignored": ignored_devices,
        "drives": drives,
    }


def register_storage_device(device_id: str, name: str = "", auto_mount: bool = True) -> dict[str, Any]:
    cfg = ensure_storage_config()
    discovered = _read_probe_devices()
    target = next((item for item in discovered if str(item.get("id") or "") == device_id), None)
    if not target:
        raise NetControlError(code="storage_device_not_found", message="Storage device not found in current scan")

    devices = cfg.get("devices") if isinstance(cfg.get("devices"), list) else []
    if any(str(item.get("id") or "") == device_id for item in devices if isinstance(item, dict)):
        return {"device_id": device_id, "already_registered": True}

    mount_path = _next_mount_path(devices, target)
    new_item = {
        "id": device_id,
        "name": (name or target.get("label") or "").strip(),
        "uuid": target.get("uuid", ""),
        "part_uuid": target.get("part_uuid", ""),
        "label": target.get("label", ""),
        "filesystem": target.get("filesystem", ""),
        "size_bytes": int(target.get("size_bytes") or 0),
        "vendor": target.get("vendor", ""),
        "model": target.get("model", ""),
        "serial": target.get("serial", ""),
        "transport": target.get("transport", ""),
        "mount_path": mount_path,
        "mount_strategy": "persistent",
        "mount_options": "defaults,noatime,nofail",
        "is_enabled": True,
        "auto_mount": bool(auto_mount),
        "allow_portal_storage": False,
        "allow_media_storage": True,
        "added_at": utc_now(),
        "last_seen_at": utc_now(),
        "last_seen_device_path": target.get("device_path", ""),
        "last_known_present": True,
        "last_error": "",
        "notes": "",
    }
    devices.append(new_item)
    cfg["devices"] = devices
    cfg["ignored"] = [entry for entry in (cfg.get("ignored") or []) if entry != device_id]
    ok, err = save_storage_config(cfg)
    if not ok:
        raise NetControlError(code="storage_config_write_failed", message="Could not persist storage registration", detail=err)
    log_event("storage", "Storage device registered", data={"id": device_id, "mount_path": mount_path})
    return {"device_id": device_id, "mount_path": mount_path, "registered": True}


def ignore_storage_device(device_id: str) -> dict[str, Any]:
    cfg = ensure_storage_config()
    ignored = cfg.get("ignored") if isinstance(cfg.get("ignored"), list) else []
    if device_id not in ignored:
        ignored.append(device_id)
    cfg["ignored"] = ignored
    ok, err = save_storage_config(cfg)
    if not ok:
        raise NetControlError(code="storage_config_write_failed", message="Could not persist ignored storage device", detail=err)
    return {"device_id": device_id, "ignored": True}


def unignore_storage_device(device_id: str) -> dict[str, Any]:
    cfg = ensure_storage_config()
    ignored = cfg.get("ignored") if isinstance(cfg.get("ignored"), list) else []
    before = len(ignored)
    ignored = [entry for entry in ignored if str(entry) != device_id]
    cfg["ignored"] = ignored
    ok, err = save_storage_config(cfg)
    if not ok:
        raise NetControlError(code="storage_config_write_failed", message="Could not persist unignore operation", detail=err)
    if len(ignored) == before:
        raise NetControlError(code="storage_device_not_ignored", message="Storage device is not ignored")
    return {"device_id": device_id, "ignored": False}


def remove_storage_device(device_id: str) -> dict[str, Any]:
    cfg = ensure_storage_config()
    devices = cfg.get("devices") if isinstance(cfg.get("devices"), list) else []
    before = len(devices)
    devices = [item for item in devices if str(item.get("id") or "") != device_id]
    cfg["devices"] = devices
    ok, err = save_storage_config(cfg)
    if not ok:
        raise NetControlError(code="storage_config_write_failed", message="Could not persist storage removal", detail=err)
    if len(devices) == before:
        raise NetControlError(code="storage_device_not_found", message="Storage device not found")
    log_event("storage", "Storage device removed", data={"id": device_id})
    return {"device_id": device_id, "removed": True}


def _mutate_device(device_id: str, mutator) -> dict[str, Any]:
    cfg = ensure_storage_config()
    devices = cfg.get("devices") if isinstance(cfg.get("devices"), list) else []
    target: dict[str, Any] | None = None
    for item in devices:
        if str(item.get("id") or "") == device_id:
            target = item
            break
    if not target:
        raise NetControlError(code="storage_device_not_found", message="Storage device not found")
    result = mutator(target)
    ok, err = save_storage_config(cfg)
    if not ok:
        raise NetControlError(code="storage_config_write_failed", message="Could not persist storage change", detail=err)
    return result


def set_storage_enabled(device_id: str, enabled: bool) -> dict[str, Any]:
    def _apply(target: dict[str, Any]) -> dict[str, Any]:
        target["is_enabled"] = bool(enabled)
        if not enabled:
            target["auto_mount"] = False
        target["last_error"] = ""
        return {"device_id": device_id, "is_enabled": bool(enabled), "auto_mount": bool(target.get("auto_mount", False))}

    res = _mutate_device(device_id, _apply)
    log_event("storage", "Storage device toggled", data=res)
    return res


def set_storage_auto_mount(device_id: str, auto_mount: bool) -> dict[str, Any]:
    def _apply(target: dict[str, Any]) -> dict[str, Any]:
        target["auto_mount"] = bool(auto_mount)
        if auto_mount:
            target["is_enabled"] = True
        target["last_error"] = ""
        return {"device_id": device_id, "auto_mount": bool(auto_mount), "is_enabled": bool(target.get("is_enabled", True))}

    res = _mutate_device(device_id, _apply)
    log_event("storage", "Storage automount toggled", data=res)
    return res


def mount_storage_device(device_id: str) -> dict[str, Any]:
    cfg = ensure_storage_config()
    devices = cfg.get("devices") if isinstance(cfg.get("devices"), list) else []
    target = next((item for item in devices if str(item.get("id") or "") == device_id), None)
    if not target:
        raise NetControlError(code="storage_device_not_found", message="Storage device not found")
    selector_type = "uuid" if str(target.get("uuid") or "").strip() else "partuuid"
    selector_value = str(target.get("uuid") if selector_type == "uuid" else target.get("part_uuid") or "").strip()
    if not selector_value:
        raise NetControlError(code="storage_identity_missing", message="Storage device has no UUID/PARTUUID")
    mount_path = str(target.get("mount_path") or "").strip()
    if not mount_path:
        raise NetControlError(code="storage_mount_path_missing", message="Storage device has no mount path configured")
    try:
        result = storage_mount(selector_type=selector_type, selector_value=selector_value, mount_path=mount_path, mount_options=str(target.get("mount_options") or "defaults,noatime,nofail"))
        target["last_error"] = ""
        target["last_known_present"] = True
        target["last_seen_at"] = utc_now()
        save_storage_config(cfg)
        log_event("storage", "Storage device mounted", data={"id": device_id, "mount_path": mount_path})
        return {"device_id": device_id, "mount_path": mount_path, "mounted": bool(result.get("mounted", True))}
    except NetControlError as exc:
        target["last_error"] = exc.detail or exc.message
        save_storage_config(cfg)
        raise


def unmount_storage_device(device_id: str) -> dict[str, Any]:
    cfg = ensure_storage_config()
    devices = cfg.get("devices") if isinstance(cfg.get("devices"), list) else []
    target = next((item for item in devices if str(item.get("id") or "") == device_id), None)
    if not target:
        raise NetControlError(code="storage_device_not_found", message="Storage device not found")
    mount_path = str(target.get("mount_path") or "").strip()
    if not mount_path:
        raise NetControlError(code="storage_mount_path_missing", message="Storage device has no mount path configured")
    try:
        result = storage_unmount(mount_path=mount_path)
        target["last_error"] = ""
        save_storage_config(cfg)
        log_event("storage", "Storage device unmounted", data={"id": device_id, "mount_path": mount_path})
        return {"device_id": device_id, "mount_path": mount_path, "mounted": bool(result.get("mounted", False))}
    except NetControlError as exc:
        target["last_error"] = exc.detail or exc.message
        save_storage_config(cfg)
        raise


def dump_storage_config() -> str:
    cfg = ensure_storage_config()
    return json.dumps(cfg, ensure_ascii=False, indent=2)
