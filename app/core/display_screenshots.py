from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests

from app.core.device import ensure_device
from app.core.netcontrol import NetControlError
from app.core.storage_config import ensure_storage_config
from app.core.storage_file_manager import StorageFileManagerService
from app.core.timeutil import utc_now


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_SCREENSHOT_DIR_NAME = "screenshots"
_DEFAULT_TIMEOUT = 20


@dataclass(frozen=True)
class ScreenshotInfo:
    connector: str
    file_path: Path
    relative_path: str
    updated_at: str
    size_bytes: int
    url: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": True,
            "connector": self.connector,
            "relative_path": self.relative_path,
            "updated_at": self.updated_at,
            "size_bytes": self.size_bytes,
            "url": self.url,
        }


def _sanitize_connector(connector: str) -> str:
    value = str(connector or "").strip()
    if not value:
        return "unknown"
    value = _SAFE_NAME_RE.sub("_", value)
    return value[:64] or "unknown"


def _iso_from_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _resolve_internal_root() -> tuple[str, Path]:
    cfg = ensure_storage_config()
    internal_cfg = cfg.get("internal") if isinstance(cfg.get("internal"), dict) else {}
    internal_id = str(internal_cfg.get("id") or "internal-media").strip() or "internal-media"
    root = StorageFileManagerService().resolve_root(internal_id)
    return root.device_id, root.mount_path


def _ensure_screenshot_dir() -> tuple[str, Path, Path]:
    device_id, mount_path = _resolve_internal_root()
    target = (mount_path / _SCREENSHOT_DIR_NAME).resolve()
    target.mkdir(parents=True, exist_ok=True)
    return device_id, mount_path, target


def _build_filename(connector: str) -> str:
    safe = _sanitize_connector(connector)
    return f"display-{safe}.png"


def get_screenshot_path(connector: str) -> Path:
    _, _, target = _ensure_screenshot_dir()
    return target / _build_filename(connector)


def get_screenshot_info(connector: str, *, cache_bust: bool = True) -> ScreenshotInfo | None:
    try:
        _, mount_path, target_dir = _ensure_screenshot_dir()
    except Exception:
        return None

    file_path = (target_dir / _build_filename(connector)).resolve(strict=False)
    if not file_path.exists() or not file_path.is_file():
        return None

    try:
        stat = file_path.stat()
    except Exception:
        return None

    rel = file_path.relative_to(mount_path).as_posix()
    updated_at = _iso_from_ts(stat.st_mtime)
    ts = int(stat.st_mtime)
    url = f"/api/display/screenshot/{_sanitize_connector(connector)}"
    if cache_bust:
        url = f"{url}?ts={ts}"
    return ScreenshotInfo(
        connector=str(connector or ""),
        file_path=file_path,
        relative_path=rel,
        updated_at=updated_at,
        size_bytes=int(stat.st_size),
        url=url,
    )


def delete_screenshot(connector: str) -> bool:
    try:
        file_path = get_screenshot_path(connector)
    except Exception:
        return False
    if file_path.exists() and file_path.is_file():
        try:
            file_path.unlink()
            return True
        except Exception:
            return False
    return False


def clear_all_screenshots() -> int:
    try:
        _, _, target_dir = _ensure_screenshot_dir()
    except Exception:
        return 0
    removed = 0
    for item in target_dir.glob("display-*.png"):
        try:
            item.unlink()
            removed += 1
        except Exception:
            continue
    return removed


def _command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def _run_capture(cmd: list[str] | str, *, shell: bool = False) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            cmd,
            shell=shell,
            capture_output=True,
            text=True,
            timeout=_DEFAULT_TIMEOUT,
            env=os.environ.copy(),
        )
    except Exception as exc:
        return False, str(exc)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        return False, err or f"exit={proc.returncode}"
    return True, ""


def capture_screenshot(connector: str) -> Path:
    file_path = get_screenshot_path(connector)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    if file_path.exists():
        try:
            file_path.unlink()
        except Exception:
            pass

    output = str(file_path)
    safe_connector = _sanitize_connector(connector)

    attempts: list[tuple[list[str] | str, bool]] = []

    if _command_exists("grim"):
        if safe_connector:
            attempts.append((["grim", "-o", safe_connector, output], False))
        attempts.append((["grim", output], False))

    if _command_exists("import"):
        attempts.append((["import", "-window", "root", output], False))

    if _command_exists("scrot"):
        attempts.append((["scrot", "-o", output], False))

    if _command_exists("xwd") and _command_exists("convert"):
        attempts.append((f"xwd -root -silent | convert xwd:- '{output}'", True))

    # ffmpeg x11grab (requires DISPLAY to be set)
    if _command_exists("ffmpeg"):
        display_env = os.environ.get("DISPLAY", ":0")
        attempts.append((
            ["ffmpeg", "-y", "-f", "x11grab", "-i", f"{display_env}.0+0,0",
             "-frames:v", "1", "-q:v", "2", output],
            False,
        ))

    # ffmpeg fbdev (framebuffer — works without X11)
    if _command_exists("ffmpeg"):
        fbdev = os.environ.get("FBDEV", "/dev/fb0")
        if Path(fbdev).exists():
            attempts.append((
                ["ffmpeg", "-y", "-f", "fbdev", "-i", fbdev,
                 "-frames:v", "1", "-q:v", "2", output],
                False,
            ))

    last_error = ""
    for cmd, use_shell in attempts:
        ok, err = _run_capture(cmd, shell=use_shell)
        if ok and file_path.exists():
            return file_path
        if err:
            last_error = err

    raise NetControlError(
        code="screenshot_capture_failed",
        message="Screenshot capture failed",
        detail=last_error or "no_capture_tool_available",
    )


def upload_screenshot(cfg: dict, connector: str, file_path: Path) -> dict[str, Any]:
    panel_cfg = cfg.get("panel_screenshot_upload") if isinstance(cfg.get("panel_screenshot_upload"), dict) else {}
    url = str(panel_cfg.get("url") or "").strip()
    token = str(panel_cfg.get("token") or "").strip()
    if not url or not token:
        return {"ok": False, "error": "not_configured"}

    dev = ensure_device()
    device_uuid = str(dev.get("device_uuid") or "").strip()
    if not device_uuid:
        return {"ok": False, "error": "device_uuid_missing"}

    try:
        with file_path.open("rb") as fh:
            resp = requests.post(
                url,
                headers={"X-Portal-Token": token},
                data={
                    "device_uuid": device_uuid,
                    "display_key": str(connector or "").strip(),
                    "hostname": socket.gethostname(),
                },
                files={"file": (file_path.name, fh, "image/png")},
                timeout=20,
            )
    except Exception as exc:
        return {"ok": False, "error": "upload_failed", "detail": str(exc)}

    if resp.status_code >= 400:
        return {"ok": False, "error": "upload_failed", "detail": resp.text[:300]}

    return {"ok": True}


def capture_and_upload(cfg: dict, connector: str, *, allow_upload: bool = True) -> dict[str, Any]:
    file_path = capture_screenshot(connector)
    info = get_screenshot_info(connector)
    payload: dict[str, Any] = {"screenshot": info.to_dict() if info else {"available": False}}
    if allow_upload:
        payload["upload"] = upload_screenshot(cfg, connector, file_path)
    return payload


def maybe_auto_capture(cfg: dict, connector: str) -> dict[str, Any] | None:
    settings = cfg.get("panel_screenshot_settings") if isinstance(cfg.get("panel_screenshot_settings"), dict) else {}
    min_interval = int(settings.get("min_interval_sec") or 0)
    warmup_enabled = bool(settings.get("warmup_enabled", True))
    if not warmup_enabled:
        return None
    last_ts = str(settings.get("last_captured_at") or "").strip()
    if last_ts and min_interval > 0:
        try:
            last_dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) < last_dt + timedelta(seconds=min_interval):
                return None
        except Exception:
            pass
    payload = capture_and_upload(cfg, connector, allow_upload=True)
    settings["last_captured_at"] = utc_now()
    cfg["panel_screenshot_settings"] = settings
    return payload
