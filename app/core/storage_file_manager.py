from __future__ import annotations

import mimetypes
import posixpath
import shutil
import stat
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from app.core.netcontrol import NetControlError
from app.core.storage_config import ensure_storage_config

try:
    from PIL import Image
except Exception:  # pragma: no cover - optional dependency
    Image = None

_TEXT_MIME_PREFIXES = ("text/", "application/json", "application/xml", "application/javascript")
_MAX_REL_PATH_LEN = 1024
_MAX_FILENAME_LEN = 255
_TEXT_MAX_BYTES = 512 * 1024
_TEXT_PREVIEW_MAX_CHARS = 12000
_IMAGE_MAX_BYTES = 8 * 1024 * 1024
_IMAGE_MAX_PIXELS = 40_000_000
_IMAGE_MAX_SIDE = 9000
_PDF_MAX_BYTES = 12 * 1024 * 1024
_FILE_ROUTE_MAX_BYTES = 16 * 1024 * 1024
_UPLOAD_MAX_FILES = 100
_UPLOAD_MAX_FILE_BYTES = 512 * 1024 * 1024
_DIR_SAMPLE_LIMIT = 8
_DELETE_CONFIRM_WORD = "DELETE"


@dataclass(frozen=True)
class StorageRoot:
    device_id: str
    mount_path: Path
    display_name: str


class StorageFileManagerService:
    def sanitize_relative_path(self, relative_path: str) -> str:
        raw = str(relative_path or "")
        if "\x00" in raw:
            raise NetControlError(code="storage_path_invalid", message="Invalid path")
        raw = raw.replace("\\", "/").strip()
        if len(raw) > _MAX_REL_PATH_LEN:
            raise NetControlError(code="storage_path_too_long", message="Path is too long")
        if raw == "":
            return ""
        if raw.startswith("/"):
            raise NetControlError(code="storage_path_forbidden", message="Absolute paths are not allowed")

        normalized = posixpath.normpath(raw)
        if normalized in (".", ""):
            return ""
        if normalized.startswith("../") or normalized == "..":
            raise NetControlError(code="storage_path_forbidden", message="Path is outside the allowed storage root")

        pure = PurePosixPath(normalized)
        cleaned_parts: list[str] = []
        for part in pure.parts:
            if part in ("", "."):
                continue
            if part == "..":
                raise NetControlError(code="storage_path_forbidden", message="Path is outside the allowed storage root")
            if len(part) > _MAX_FILENAME_LEN:
                raise NetControlError(code="storage_path_invalid", message="Path segment is too long")
            cleaned_parts.append(part)
        return "/".join(cleaned_parts)

    def resolve_root(self, device_id: str) -> StorageRoot:
        device_key = str(device_id or "").strip()
        if not device_key:
            raise NetControlError(code="invalid_storage_device", message="Storage device is required")

        cfg = ensure_storage_config()
        internal_cfg = cfg.get("internal") if isinstance(cfg.get("internal"), dict) else {}
        internal_id = str(internal_cfg.get("id") or "internal-media")
        if device_key == internal_id:
            mount_path_raw = str(internal_cfg.get("mount_path") or "/mnt/deviceportal/media").strip()
            mount_path = Path(mount_path_raw)
            if not mount_path.is_absolute():
                raise NetControlError(code="storage_mount_path_invalid", message="Configured mount path is invalid")
            if not mount_path.exists():
                raise NetControlError(code="storage_mount_missing", message="Storage mount path is missing")
            if not self._is_mounted(mount_path):
                raise NetControlError(code="storage_not_mounted", message="Storage device is not mounted")
            display_name = str(internal_cfg.get("name") or "Interner Medienspeicher (Loop)")
            return StorageRoot(device_id=device_key, mount_path=mount_path.resolve(), display_name=display_name)

        devices = cfg.get("devices") if isinstance(cfg.get("devices"), list) else []
        target = next((item for item in devices if str(item.get("id") or "") == device_key), None)
        if not isinstance(target, dict):
            raise NetControlError(code="storage_device_not_found", message="Storage device not found")

        mount_path_raw = str(target.get("mount_path") or "").strip()
        if not mount_path_raw:
            raise NetControlError(code="storage_mount_path_missing", message="Storage device has no mount path configured")
        mount_path = Path(mount_path_raw)
        if not mount_path.is_absolute():
            raise NetControlError(code="storage_mount_path_invalid", message="Configured mount path is invalid")
        if not mount_path.exists():
            raise NetControlError(code="storage_mount_missing", message="Storage mount path is missing")
        if not self._is_mounted(mount_path):
            raise NetControlError(code="storage_not_mounted", message="Storage device is not mounted")

        display_name = str(target.get("name") or target.get("label") or target.get("uuid") or target.get("id") or "Storage")
        return StorageRoot(device_id=device_key, mount_path=mount_path.resolve(), display_name=display_name)

    def resolve_relative_path(self, root: StorageRoot, relative_path: str) -> Path:
        rel = self.sanitize_relative_path(relative_path)
        if rel == "":
            candidate = root.mount_path
        else:
            candidate = (root.mount_path / PurePosixPath(rel)).resolve(strict=False)

        root_real = root.mount_path
        if candidate != root_real and root_real not in candidate.parents:
            raise NetControlError(code="storage_path_forbidden", message="Path is outside the allowed storage root")
        self._assert_no_symlink_on_path(root, candidate)
        return candidate

    def relative_to_root(self, root: StorageRoot, path: Path) -> str:
        try:
            rel = path.resolve(strict=False).relative_to(root.mount_path)
            as_posix = rel.as_posix()
            return "" if as_posix == "." else as_posix
        except Exception as exc:
            raise NetControlError(code="storage_path_forbidden", message="Path is outside the allowed storage root") from exc

    def _assert_no_symlink_on_path(self, root: StorageRoot, path: Path) -> None:
        root_real = root.mount_path
        if path == root_real:
            return
        rel = path.relative_to(root_real)
        probe = root_real
        for part in rel.parts:
            probe = probe / part
            if not probe.exists() and probe != path:
                continue
            try:
                st = probe.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(st.st_mode):
                raise NetControlError(code="storage_symlink_blocked", message="Symlink entries are blocked for security")

    def list_tree(self, device_id: str, relative_path: str = "") -> dict[str, Any]:
        root = self.resolve_root(device_id)
        current = self.resolve_relative_path(root, relative_path)
        if not current.exists():
            raise NetControlError(code="storage_path_not_found", message="Directory not found")
        if current.is_symlink():
            raise NetControlError(code="storage_symlink_blocked", message="Symlink entries are blocked for security")
        if not current.is_dir():
            raise NetControlError(code="storage_path_not_directory", message="Path is not a directory")

        breadcrumb = self._build_breadcrumb(root, current)
        directories: list[dict[str, Any]] = []
        for entry in sorted(current.iterdir(), key=lambda p: p.name.lower()):
            rel = self.relative_to_root(root, entry)
            is_symlink = entry.is_symlink()
            if is_symlink:
                directories.append(
                    {
                        "name": entry.name,
                        "path": rel,
                        "is_symlink": True,
                        "blocked": True,
                    }
                )
                continue
            if not entry.is_dir():
                continue
            directories.append(
                {
                    "name": entry.name,
                    "path": rel,
                    "is_symlink": False,
                    "blocked": False,
                }
            )
        return {
            "device_id": root.device_id,
            "device_name": root.display_name,
            "root_path": str(root.mount_path),
            "current_path": self.relative_to_root(root, current),
            "breadcrumb": breadcrumb,
            "directories": directories,
        }

    def list_directory(self, device_id: str, relative_path: str = "") -> dict[str, Any]:
        root = self.resolve_root(device_id)
        current = self.resolve_relative_path(root, relative_path)
        if not current.exists():
            raise NetControlError(code="storage_path_not_found", message="Directory not found")
        if current.is_symlink():
            raise NetControlError(code="storage_symlink_blocked", message="Symlink entries are blocked for security")
        if not current.is_dir():
            raise NetControlError(code="storage_path_not_directory", message="Path is not a directory")

        entries: list[dict[str, Any]] = []
        for entry in sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            rel = self.relative_to_root(root, entry)
            st = entry.lstat()
            is_symlink = stat.S_ISLNK(st.st_mode)
            is_dir = stat.S_ISDIR(st.st_mode)
            is_file = stat.S_ISREG(st.st_mode)
            entry_type = "symlink" if is_symlink else ("directory" if is_dir else ("file" if is_file else "other"))
            entries.append(
                {
                    "name": entry.name,
                    "path": rel,
                    "type": entry_type,
                    "size_bytes": 0 if is_dir else int(st.st_size),
                    "modified_at": self._iso_utc(st.st_mtime),
                    "is_symlink": is_symlink,
                    "blocked": is_symlink,
                }
            )

        return {
            "device_id": root.device_id,
            "device_name": root.display_name,
            "root_path": str(root.mount_path),
            "current_path": self.relative_to_root(root, current),
            "entries": entries,
        }

    def preview(self, device_id: str, relative_path: str) -> dict[str, Any]:
        root = self.resolve_root(device_id)
        target = self.resolve_relative_path(root, relative_path)
        if not target.exists():
            raise NetControlError(code="storage_path_not_found", message="Entry not found")

        rel = self.relative_to_root(root, target)
        st = target.lstat()
        is_dir = stat.S_ISDIR(st.st_mode)
        is_file = stat.S_ISREG(st.st_mode)
        mime, _ = mimetypes.guess_type(target.name)
        mime = mime or "application/octet-stream"
        out: dict[str, Any] = {
            "device_id": root.device_id,
            "device_name": root.display_name,
            "name": target.name,
            "path": rel,
            "type": "directory" if is_dir else ("file" if is_file else "other"),
            "mime_type": mime,
            "size_bytes": int(st.st_size) if not is_dir else 0,
            "modified_at": self._iso_utc(st.st_mtime),
            "is_symlink": target.is_symlink(),
            "preview_kind": "info",
        }
        if target.is_symlink():
            out["preview_kind"] = "blocked"
            out["preview_message"] = "Symlink ist blockiert."
            return out

        if is_dir:
            children = []
            for idx, child in enumerate(sorted(target.iterdir(), key=lambda p: p.name.lower())):
                if idx >= _DIR_SAMPLE_LIMIT:
                    break
                children.append(child.name)
            out["children_preview"] = children
            return out

        if not is_file:
            return out

        size = int(st.st_size)
        is_text = any(mime.startswith(prefix) for prefix in _TEXT_MIME_PREFIXES)
        if is_text and size <= _TEXT_MAX_BYTES:
            try:
                out["preview_kind"] = "text"
                out["text_excerpt"] = target.read_text(encoding="utf-8", errors="replace")[:_TEXT_PREVIEW_MAX_CHARS]
                out["preview_message"] = (
                    "Textvorschau gekürzt."
                    if len(out["text_excerpt"]) >= _TEXT_PREVIEW_MAX_CHARS
                    else ""
                )
                return out
            except Exception:
                pass
        elif is_text and size > _TEXT_MAX_BYTES:
            out["preview_kind"] = "too_large"
            out["preview_message"] = "Datei zu groß für Textvorschau."
            return out

        if mime.startswith("image/") and size <= _IMAGE_MAX_BYTES:
            width, height = self._image_dimensions(target)
            if width and height:
                out["image_width"] = width
                out["image_height"] = height
                if (width * height) > _IMAGE_MAX_PIXELS or width > _IMAGE_MAX_SIDE or height > _IMAGE_MAX_SIDE:
                    out["preview_kind"] = "too_large"
                    out["preview_message"] = "Bilddimensionen zu groß für Vorschau."
                    return out
            out["preview_kind"] = "image"
            out["file_url"] = f"/api/network/storage/file-manager/file?device_id={root.device_id}&path={rel}"
            return out
        if mime.startswith("image/") and size > _IMAGE_MAX_BYTES:
            out["preview_kind"] = "too_large"
            out["preview_message"] = "Bilddatei zu groß für Vorschau."
            return out

        if mime == "application/pdf" and size <= _PDF_MAX_BYTES:
            out["preview_kind"] = "pdf"
            out["file_url"] = f"/api/network/storage/file-manager/file?device_id={root.device_id}&path={rel}"
            return out
        if mime == "application/pdf" and size > _PDF_MAX_BYTES:
            out["preview_kind"] = "too_large"
            out["preview_message"] = "PDF-Datei zu groß für Vorschau."
            return out

        out["preview_kind"] = "binary"
        out["preview_message"] = "Vorschau für diesen Dateityp nicht unterstützt."
        return out

    def resolve_downloadable_file(self, device_id: str, relative_path: str) -> tuple[Path, str]:
        root = self.resolve_root(device_id)
        target = self.resolve_relative_path(root, relative_path)
        if not target.exists():
            raise NetControlError(code="storage_path_not_found", message="File not found")
        if target.is_symlink():
            raise NetControlError(code="storage_symlink_blocked", message="Symlink entries are blocked for security")
        if not target.is_file():
            raise NetControlError(code="storage_path_not_file", message="Path is not a file")
        mime, _ = mimetypes.guess_type(target.name)
        st = target.stat()
        if int(st.st_size) > _FILE_ROUTE_MAX_BYTES:
            raise NetControlError(code="storage_preview_too_large", message="File too large for preview endpoint")
        return target, (mime or "application/octet-stream")

    def upload_files(self, device_id: str, relative_path: str, files: list[FileStorage]) -> dict[str, Any]:
        if not files:
            raise NetControlError(code="storage_upload_no_files", message="No files provided")
        if len(files) > _UPLOAD_MAX_FILES:
            raise NetControlError(code="storage_upload_too_many_files", message="Too many files in one upload")

        root = self.resolve_root(device_id)
        target_dir = self.resolve_relative_path(root, relative_path)
        if not target_dir.exists():
            raise NetControlError(code="storage_path_not_found", message="Directory not found")
        if target_dir.is_symlink():
            raise NetControlError(code="storage_symlink_blocked", message="Symlink entries are blocked for security")
        if not target_dir.is_dir():
            raise NetControlError(code="storage_path_not_directory", message="Upload target is not a directory")

        uploaded: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []

        for file_item in files:
            if not isinstance(file_item, FileStorage):
                continue
            original_name = str(file_item.filename or "").strip()
            safe_name = secure_filename(original_name)
            if not safe_name:
                skipped.append({"file": original_name or "-", "reason": "invalid filename"})
                continue

            declared_size = int(file_item.content_length or 0)
            if declared_size > _UPLOAD_MAX_FILE_BYTES:
                skipped.append({"file": original_name or safe_name, "reason": "file too large"})
                continue

            destination = self._next_available_name(target_dir, safe_name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                file_item.save(str(destination))
            except PermissionError as exc:
                raise NetControlError(
                    code="storage_upload_write_failed",
                    message="Upload failed: permission denied",
                    detail=str(exc),
                ) from exc
            except OSError as exc:
                raise NetControlError(
                    code="storage_upload_write_failed",
                    message="Upload failed while writing file",
                    detail=str(exc),
                ) from exc

            try:
                written_size = int(destination.stat().st_size)
            except OSError as exc:
                raise NetControlError(
                    code="storage_upload_stat_failed",
                    message="Upload finished but file metadata could not be read",
                    detail=str(exc),
                ) from exc

            if written_size <= 0:
                destination.unlink(missing_ok=True)
                skipped.append({"file": original_name or safe_name, "reason": "empty file"})
                continue
            if written_size > _UPLOAD_MAX_FILE_BYTES:
                destination.unlink(missing_ok=True)
                skipped.append({"file": original_name or safe_name, "reason": "file too large"})
                continue

            rel = self.relative_to_root(root, destination)
            uploaded.append(
                {
                    "name": destination.name,
                    "original_name": original_name or destination.name,
                    "path": rel,
                    "size_bytes": written_size,
                }
            )

        if not uploaded and skipped:
            raise NetControlError(code="storage_upload_failed", message="No files uploaded", detail=skipped[0]["reason"])

        return {
            "device_id": root.device_id,
            "target_path": self.relative_to_root(root, target_dir),
            "uploaded": uploaded,
            "uploaded_count": len(uploaded),
            "skipped": skipped,
            "skipped_count": len(skipped),
        }

    def _build_breadcrumb(self, root: StorageRoot, current: Path) -> list[dict[str, str]]:
        breadcrumb: list[dict[str, str]] = [{"name": root.display_name, "path": ""}]
        rel = self.relative_to_root(root, current)
        if not rel:
            return breadcrumb
        parts = rel.split("/")
        assembled: list[str] = []
        for part in parts:
            assembled.append(part)
            breadcrumb.append({"name": part, "path": "/".join(assembled)})
        return breadcrumb

    @staticmethod
    def _image_dimensions(path: Path) -> tuple[int, int]:
        if Image is None:
            return 0, 0
        try:
            with Image.open(path) as img:
                width, height = img.size
                return int(width), int(height)
        except Exception:
            return 0, 0

    @staticmethod
    def _next_available_name(base_dir: Path, filename: str) -> Path:
        candidate = base_dir / filename
        if not candidate.exists():
            return candidate
        stem = candidate.stem
        suffix = candidate.suffix
        idx = 2
        while True:
            next_candidate = base_dir / f"{stem}-{idx}{suffix}"
            if not next_candidate.exists():
                return next_candidate
            idx += 1

    @staticmethod
    def _is_mounted(path: Path) -> bool:
        try:
            out = subprocess.check_output(
                ["findmnt", "-rn", "-M", str(path), "-o", "TARGET"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            return out == str(path)
        except Exception:
            return False

    @staticmethod
    def _iso_utc(timestamp: float) -> str:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class StorageDeleteService:
    def __init__(self, fm_service: StorageFileManagerService | None = None):
        self.fm_service = fm_service or StorageFileManagerService()

    def delete_selected(
        self,
        device_id: str,
        selected_paths: list[str],
        confirm_word: str = "",
        confirm_count: int = 0,
    ) -> dict[str, Any]:
        if not isinstance(selected_paths, list) or not selected_paths:
            raise NetControlError(code="invalid_delete_selection", message="No storage entries selected")
        clean_paths = [self.fm_service.sanitize_relative_path(item) for item in selected_paths if str(item or "").strip()]
        if not clean_paths:
            raise NetControlError(code="invalid_delete_selection", message="No storage entries selected")
        if str(confirm_word or "").strip().upper() != _DELETE_CONFIRM_WORD:
            raise NetControlError(code="storage_delete_confirmation_missing", message="Delete confirmation is required")
        if int(confirm_count or 0) != len(clean_paths):
            raise NetControlError(code="storage_delete_confirmation_mismatch", message="Delete confirmation count mismatch")

        root = self.fm_service.resolve_root(device_id)
        deleted: list[str] = []
        failed: list[dict[str, str]] = []

        for rel in clean_paths:
            try:
                target = self.fm_service.resolve_relative_path(root, rel)
                if target == root.mount_path:
                    raise NetControlError(code="storage_delete_root_blocked", message="Storage root cannot be deleted")
                if not target.exists():
                    raise NetControlError(code="storage_path_not_found", message="Entry not found")
                if target.is_symlink():
                    raise NetControlError(code="storage_symlink_blocked", message="Symlink entries are blocked for security")
                if target.is_file():
                    target.unlink()
                elif target.is_dir():
                    shutil.rmtree(target)
                else:
                    raise NetControlError(code="storage_delete_not_supported", message="Entry type cannot be deleted")
                deleted.append(rel)
            except NetControlError as exc:
                failed.append({"path": rel, "message": exc.message})
            except Exception as exc:
                failed.append({"path": rel, "message": str(exc)})

        if not deleted and failed:
            raise NetControlError(code="storage_delete_failed", message="Could not delete selected entries", detail=failed[0]["message"])
        return {
            "device_id": root.device_id,
            "deleted_paths": deleted,
            "failed": failed,
            "deleted_count": len(deleted),
            "failed_count": len(failed),
        }
