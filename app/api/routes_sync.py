from __future__ import annotations

import hmac
import re
import threading
import time

from flask import Blueprint, jsonify, request

from app.api import routes_panel
from app.core.config import _safe_base_url, ensure_config
from app.core.device import ensure_device
from app.core.fingerprint import collect_fingerprint
from app.core.httpclient import http_post_json
from app.core.jsonio import write_json
from app.core.paths import CONFIG_PATH
from app.core.auth_session import is_authenticated
from app.core.systeminfo import get_hostname, get_ip
from app.core.timeutil import utc_now
from app.core.overlay_state import (
    read_overlay_state,
    sanitize_flash,
    sanitize_ticker,
    sanitize_popup,
    write_overlay_state,
)
from app.core.netcontrol import (
    NetControlError,
    player_service_action,
    player_update,
    portal_update,
    restart_portal_service,
    system_power_action,
)
from app.core.sentinel_manager import (
    SentinelManagerError,
    get_status as sentinels_status,
    install_sentinel as install_sentinel_module,
    uninstall_sentinel as uninstall_sentinel_module,
)

bp_sync = Blueprint("sync", __name__)
_overlay_flash_autoclear_lock = threading.Lock()


def _bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _orientation_to_rotation(value: object) -> int:
    raw = str(value or "").strip().lower()
    mapping = {
        "horizontal": 0,
        "vertical": 90,
        "rotated_right": 90,
        "rotated_left": -90,
        "upside_down": 180,
    }
    return mapping.get(raw, 0)


def _normalize_rotation_degrees(value: object) -> int:
    try:
        raw = int(float(value))
    except Exception:
        raw = 0

    normalized = raw % 360
    if normalized < 0:
        normalized += 360

    if normalized >= 315 or normalized < 45:
        return 0
    if normalized >= 45 and normalized < 135:
        return 90
    if normalized >= 135 and normalized < 225:
        return 180
    return 270


def _normalize_overlay_item_with_orientation(item: dict, item_type: str, display_rotation_degrees: int = 0) -> dict:
    payload = dict(item)
    # Popups can arrive with pre-rotated media from Adminpanel.
    popup_pre_rotated = item_type == "popup" and _bool(payload.get("preRotated"), default=False)
    base_rotation = payload.get("rotation")
    if base_rotation is None and "orientation" in payload:
        base_rotation = _orientation_to_rotation(payload.get("orientation"))
    try:
        base_rotation_i = int(float(base_rotation)) if base_rotation is not None else 0
    except Exception:
        base_rotation_i = 0
    payload["rotation"] = 0 if popup_pre_rotated else _normalize_rotation_degrees(base_rotation_i + int(display_rotation_degrees or 0))

    if item_type == "flash":
        return sanitize_flash(payload)
    if item_type == "ticker":
        return sanitize_ticker(payload)
    if item_type == "popup":
        return sanitize_popup(payload)
    return payload


def _apply_overlay_state_from_admin(params: dict) -> tuple[bool, dict]:
    overlay_state = params.get("overlayState") if isinstance(params.get("overlayState"), dict) else {}
    if not overlay_state:
        return False, {"error": "overlay_state_missing"}

    include_popups = _bool(params.get("includePopups"), default=True)
    write_mode = str(params.get("writeMode") or "replace").strip().lower()
    if write_mode not in {"replace", "merge"}:
        write_mode = "replace"

    current_state, _ = read_overlay_state()
    next_state = {
        "updatedAt": utc_now(),
        "flashMessages": [],
        "tickers": [],
        "popups": [],
    }

    if write_mode == "merge":
        next_state["flashMessages"] = list(current_state.get("flashMessages") or []) if isinstance(current_state.get("flashMessages"), list) else []
        next_state["tickers"] = list(current_state.get("tickers") or []) if isinstance(current_state.get("tickers"), list) else []
        next_state["popups"] = list(current_state.get("popups") or []) if isinstance(current_state.get("popups"), list) else []

    display_payload = overlay_state.get("display") if isinstance(overlay_state.get("display"), dict) else {}
    display_rotation_degrees = _normalize_rotation_degrees(
        display_payload.get("rotationDegrees")
        if isinstance(display_payload, dict) and "rotationDegrees" in display_payload
        else (
            display_payload.get("rotation_degrees")
            if isinstance(display_payload, dict)
            else params.get("displayRotationDegrees")
        )
    )

    flash_ids_for_autoclear: list[str] = []
    flash_autoclear_delay_ms = 0

    flashes = overlay_state.get("flashMessages")
    if isinstance(flashes, list):
        parsed: list[dict] = []
        enabled_durations: list[int] = []
        for row in flashes:
            if not isinstance(row, dict):
                continue
            normalized = _normalize_overlay_item_with_orientation(row, "flash", display_rotation_degrees)
            parsed.append(normalized)
            if bool(normalized.get("enabled")):
                flash_id = str(normalized.get("id") or "").strip()
                if flash_id:
                    flash_ids_for_autoclear.append(flash_id)
                try:
                    enabled_durations.append(max(500, int(normalized.get("durationMs") or 5000)))
                except Exception:
                    enabled_durations.append(5000)
        next_state["flashMessages"] = parsed if write_mode == "replace" else (next_state["flashMessages"] + parsed)
        if enabled_durations:
            # One full cycle across active flashes, plus a small tail.
            flash_autoclear_delay_ms = min(600000, max(500, sum(enabled_durations) + 750))

    tickers = overlay_state.get("tickers")
    if isinstance(tickers, list):
        parsed = []
        for row in tickers:
            if not isinstance(row, dict):
                continue
            parsed.append(_normalize_overlay_item_with_orientation(row, "ticker", display_rotation_degrees))
        next_state["tickers"] = parsed if write_mode == "replace" else (next_state["tickers"] + parsed)

    if include_popups:
        popups = overlay_state.get("popups")
        if isinstance(popups, list):
            parsed = []
            for row in popups:
                if not isinstance(row, dict):
                    continue
                popup_row = dict(row)
                # Backward-compatible mapping from Admin payload keys.
                if not popup_row.get("title"):
                    popup_row["title"] = str(popup_row.get("popupName") or "").strip()
                if not popup_row.get("message"):
                    popup_row["message"] = str(popup_row.get("popupContent") or "").strip()
                if not popup_row.get("imagePath"):
                    popup_row["imagePath"] = _extract_first_image_src(str(popup_row.get("popupContent") or ""))
                parsed.append(_normalize_overlay_item_with_orientation(popup_row, "popup", display_rotation_degrees))
            next_state["popups"] = parsed if write_mode == "replace" else (next_state["popups"] + parsed)

    ok, err, path = write_overlay_state(next_state)
    if not ok:
        return False, {"error": str(err or "overlay_write_failed"), "path": str(path)}

    flash_autoclear = _bool(params.get("flashAutoClear"), default=True)
    if flash_autoclear and flash_ids_for_autoclear and flash_autoclear_delay_ms > 0:
        _schedule_overlay_flash_autoclear(flash_ids_for_autoclear, flash_autoclear_delay_ms)

    return True, {
        "message": "Overlay-State angewendet.",
        "path": str(path),
        "writeMode": write_mode,
        "includePopups": include_popups,
        "flashAutoClear": flash_autoclear,
        "flashAutoClearDelayMs": flash_autoclear_delay_ms if flash_autoclear else 0,
        "displayRotationDegrees": display_rotation_degrees,
        "flashCount": len(next_state["flashMessages"]),
        "tickerCount": len(next_state["tickers"]),
        "popupCount": len(next_state["popups"]),
    }


def _schedule_overlay_flash_autoclear(flash_ids: list[str], delay_ms: int) -> None:
    unique_ids = [str(item).strip() for item in flash_ids if str(item).strip()]
    if not unique_ids:
        return
    delay_seconds = max(0.5, float(delay_ms) / 1000.0)

    def _worker() -> None:
        try:
            time.sleep(delay_seconds)
            with _overlay_flash_autoclear_lock:
                state, _ = read_overlay_state()
                rows = state.get("flashMessages")
                if not isinstance(rows, list):
                    return
                id_set = set(unique_ids)
                next_rows = []
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    row_id = str(row.get("id") or "").strip()
                    if row_id in id_set:
                        continue
                    next_rows.append(row)
                if len(next_rows) == len(rows):
                    return
                state["flashMessages"] = next_rows
                write_overlay_state(state)
        except Exception:
            # Intentional no-op: overlay autoclear must never break sync flow.
            return

    thread = threading.Thread(target=_worker, name="overlay-flash-autoclear", daemon=True)
    thread.start()


def _extract_first_image_src(content: str) -> str:
    text = str(content or "").strip()
    if text == "":
        return ""
    match = re.search(r"<img[^>]+src=['\"]([^'\"]+)['\"]", text, flags=re.IGNORECASE)
    if not match:
        return ""
    return str(match.group(1) or "").strip()


def _ensure_sync_state(cfg: dict) -> dict:
    state = cfg.get("panel_sync") if isinstance(cfg.get("panel_sync"), dict) else {}
    if not isinstance(state, dict):
        state = {}
    state.setdefault("enabled", False)
    state.setdefault("profile", {})
    state.setdefault("rules", [])
    state.setdefault("last_sync_at", None)
    state.setdefault("last_sync_status", None)
    state.setdefault("last_sync_message", "")
    state.setdefault("last_sync_direction", None)
    state.setdefault("last_sync_triggered_by", None)
    state.setdefault("last_pull_at", None)
    state.setdefault("last_push_at", None)
    state.setdefault("last_error", "")
    cfg["panel_sync"] = state
    return state


def _payload_auth_valid(data: dict, cfg: dict, dev: dict) -> tuple[bool, str]:
    uuid_in = str(data.get("deviceUuid") or data.get("device_uuid") or "").strip()
    auth_in = str(data.get("authKey") or data.get("auth_key") or "").strip()
    api_in = str(data.get("apiKey") or data.get("api_key") or data.get("adminApiKey") or "").strip()

    uuid_ref = str(dev.get("device_uuid") or "").strip()
    auth_ref = str(dev.get("auth_key") or "").strip()

    if not uuid_in or not uuid_ref:
        return False, "device_uuid_missing"
    if not hmac.compare_digest(uuid_in, uuid_ref):
        return False, "device_uuid_invalid"

    if auth_in and auth_ref and hmac.compare_digest(auth_in, auth_ref):
        return True, ""

    keys = cfg.get("panel_api_keys") if isinstance(cfg.get("panel_api_keys"), dict) else {}
    admin_to_raspi = str((keys.get("admin_to_raspi") if isinstance(keys, dict) else "") or "").strip()
    if admin_to_raspi and api_in and hmac.compare_digest(admin_to_raspi, api_in):
        return True, ""

    return False, "auth_invalid"


def _can_run_without_session(data: dict, cfg: dict, dev: dict) -> tuple[bool, str]:
    return _payload_auth_valid(data, cfg, dev)


def _normalize_rules(raw_rules: object) -> list[dict]:
    rules: list[dict] = []
    if not isinstance(raw_rules, list):
        return rules
    for item in raw_rules:
        if not isinstance(item, dict):
            continue
        key = str(item.get("fieldKey") or item.get("field_key") or "").strip()
        if not key:
            continue
        rules.append(
            {
                "fieldKey": key,
                "label": str(item.get("label") or key),
                "enabled": bool(item.get("enabled", item.get("isEnabled", True))),
                "direction": str(item.get("direction") or "portal_to_admin"),
                "conflictStrategy": str(item.get("conflictStrategy") or item.get("conflict_strategy") or "newest_wins"),
                "groupName": str(item.get("groupName") or item.get("group_name") or "General"),
                "sortOrder": int(item.get("sortOrder") or item.get("sort_order") or 0),
                "notes": str(item.get("notes") or ""),
            }
        )
    return rules


def _find_rule(rules: list[dict], field_key: str) -> dict | None:
    for rule in rules:
        if str(rule.get("fieldKey") or "").strip() == field_key:
            return rule
    return None


def _allow_direction(rules: list[dict], field_key: str, direction: str) -> bool:
    rule = _find_rule(rules, field_key)
    if not isinstance(rule, dict):
        return False
    if not bool(rule.get("enabled", False)):
        return False

    rule_direction = str(rule.get("direction") or "").strip().lower()
    if direction == "portal_to_admin":
        return rule_direction in {"portal_to_admin", "bidirectional", "manual_only"}
    if direction == "admin_to_portal":
        return rule_direction in {"admin_to_portal", "bidirectional", "manual_only"}
    return False


def _apply_admin_values_to_portal(cfg: dict, sync_state: dict, admin_values: dict, rules: list[dict]) -> dict:
    changed: list[str] = []

    if _allow_direction(rules, "admin.device_flags", "admin_to_portal"):
        device_flags = admin_values.get("device") if isinstance(admin_values.get("device"), dict) else {}
        panel_flags = cfg.get("panel_device_flags") if isinstance(cfg.get("panel_device_flags"), dict) else {}
        has_change = False
        if "isActive" in device_flags:
            panel_flags["is_active"] = bool(device_flags.get("isActive"))
            has_change = True
        if "isLocked" in device_flags:
            panel_flags["is_locked"] = bool(device_flags.get("isLocked"))
            has_change = True
        if has_change:
            panel_flags["updated_at"] = utc_now()
            cfg["panel_device_flags"] = panel_flags
            changed.append("admin.device_flags")

    if _allow_direction(rules, "admin.stream.selection", "admin_to_portal"):
        stream = admin_values.get("stream") if isinstance(admin_values.get("stream"), dict) else {}
        selected = str(stream.get("selectedStreamSlug") or stream.get("selected_stream_slug") or "").strip()
        if selected:
            cfg["selected_stream_slug"] = selected
            cfg["selected_stream_updated_at"] = utc_now()
            changed.append("admin.stream.selection")

    if _allow_direction(rules, "admin.sentinel.webhook", "admin_to_portal"):
        sentinel = admin_values.get("sentinel") if isinstance(admin_values.get("sentinel"), dict) else {}
        url = str(sentinel.get("webhookUrl") or sentinel.get("webhook_url") or "").strip()
        if url:
            settings = cfg.get("sentinel_settings") if isinstance(cfg.get("sentinel_settings"), dict) else {}
            settings["webhook_url"] = url
            settings["updated_at"] = utc_now()
            cfg["sentinel_settings"] = settings
            changed.append("admin.sentinel.webhook")

    if changed:
        cfg["updated_at"] = utc_now()
        write_json(CONFIG_PATH, cfg, mode=0o600)

    sync_state["last_admin_apply"] = {
        "changed": changed,
        "at": utc_now(),
    }
    return {"changed": changed}


def _build_portal_field_updates(cfg: dict, dev: dict) -> dict:
    fp = collect_fingerprint()
    host = get_hostname()
    ip = get_ip()
    payload = routes_panel._panel_sync_payload(cfg, dev, fp, host, ip)

    updates = {
        "runtime.identity": payload.get("identity") if isinstance(payload.get("identity"), dict) else {},
        "runtime.health": payload.get("health") if isinstance(payload.get("health"), dict) else {},
        "runtime.network": payload.get("network") if isinstance(payload.get("network"), dict) else {},
        "runtime.storage": payload.get("storage") if isinstance(payload.get("storage"), dict) else {},
        "runtime.software": payload.get("software") if isinstance(payload.get("software"), list) else [],
        "runtime.portal_payload": payload,
    }
    return updates


def _local_stream_sync(params: dict) -> tuple[bool, dict]:
    host = (request.host or "").strip()
    port = ""
    if ":" in host:
        _, _, host_port = host.rpartition(":")
        if host_port.isdigit():
            port = host_port

    base = f"http://127.0.0.1:{port}" if port else "http://127.0.0.1"
    url = f"{base}/api/stream/sync"
    payload = {}
    if isinstance(params, dict):
        for key in ("streamSlug", "streamName", "storageDeviceId", "admin_base_url"):
            value = params.get(key)
            if value is not None:
                payload[key] = value

    code, resp, err = http_post_json(url, payload, timeout=120)
    if code is None:
        return False, {"error": str(err or "stream_sync_failed")}

    if code < 200 or code >= 300 or not isinstance(resp, dict) or not bool(resp.get("ok")):
        detail = "stream_sync_failed"
        if isinstance(resp, dict):
            detail = str(resp.get("detail") or resp.get("message") or resp.get("error") or detail)
        return False, {"status": code, "error": detail, "response": resp if isinstance(resp, dict) else {}}

    return True, resp


def _run_admin_actions(cfg: dict, actions: list[dict], triggered_by: str) -> list[dict]:
    results: list[dict] = []
    for item in actions:
        if not isinstance(item, dict):
            continue

        action = str(item.get("action") or item.get("name") or "").strip().lower()
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        result: dict = {
            "action": action,
            "ok": False,
            "message": "",
            "data": {},
        }

        try:
            if action in {"system.reboot", "reboot"}:
                payload = system_power_action(action="reboot")
                result.update(ok=True, message="Reboot angefordert.", data=payload or {})

            elif action in {"system.portal_restart", "portal.restart", "portal_restart"}:
                payload = restart_portal_service(service_name="device-portal.service")
                result.update(ok=True, message="Portal-Neustart angefordert.", data=payload or {})

            elif action in {"player.restart", "player_restart"}:
                payload = player_service_action("restart", "joormann-media-deviceplayer.service")
                result.update(ok=True, message="Player-Neustart angefordert.", data=payload or {})

            elif action in {"system.portal_update", "portal.update", "portal_update"}:
                source = str(params.get("portal_update_url") or cfg.get("portal_update_url") or "").strip()
                if source:
                    cfg["portal_update_url"] = source
                    cfg["updated_at"] = utc_now()
                    write_json(CONFIG_PATH, cfg, mode=0o600)
                payload = portal_update(service_name="device-portal.service", update_source=source)
                result.update(ok=True, message="Portal-Update gestartet.", data=payload or {})

            elif action in {"player.update", "player_update"}:
                repo_link = str(
                    params.get("player_repo_link")
                    or params.get("player_repo_dir")
                    or cfg.get("player_repo_link")
                    or cfg.get("player_repo_dir")
                    or ""
                ).strip()
                service_name = str(params.get("player_service_name") or cfg.get("player_service_name") or "joormann-media-deviceplayer.service").strip() or "joormann-media-deviceplayer.service"
                service_user = str(params.get("player_service_user") or cfg.get("player_service_user") or "").strip()
                if not repo_link:
                    raise RuntimeError("player_repo_missing")
                payload = player_update(repo_link, service_user=service_user, service_name=service_name)
                result.update(ok=True, message="Player-Update gestartet.", data=payload or {})

            elif action in {"stream.sync", "stream_sync"}:
                ok, payload = _local_stream_sync(params)
                if not ok:
                    raise RuntimeError(str(payload.get("error") or "stream_sync_failed"))
                result.update(ok=True, message="Stream/Playlist-Sync gestartet.", data=payload)

            elif action in {"overlay.apply", "overlay_apply"}:
                ok, payload = _apply_overlay_state_from_admin(params)
                if not ok:
                    raise RuntimeError(str(payload.get("error") or "overlay_apply_failed"))
                result.update(ok=True, message="Overlay-Konfiguration angewendet.", data=payload)

            elif action in {"sentinel.install", "sentinel_install"}:
                slug = str(params.get("slug") or "").strip()
                if not slug:
                    raise RuntimeError("sentinel_slug_missing")
                settings = cfg.get("sentinel_settings") if isinstance(cfg.get("sentinel_settings"), dict) else {}
                webhook_url = str((settings or {}).get("webhook_url") or "").strip()
                payload = install_sentinel_module(slug=slug, webhook_url=webhook_url)
                result.update(ok=True, message=f"Sentinel '{slug}' installiert.", data=payload or {})

            elif action in {"sentinel.uninstall", "sentinel_uninstall"}:
                slug = str(params.get("slug") or "").strip()
                if not slug:
                    raise RuntimeError("sentinel_slug_missing")
                settings = cfg.get("sentinel_settings") if isinstance(cfg.get("sentinel_settings"), dict) else {}
                webhook_url = str((settings or {}).get("webhook_url") or "").strip()
                payload = uninstall_sentinel_module(slug=slug, webhook_url=webhook_url)
                result.update(ok=True, message=f"Sentinel '{slug}' entfernt.", data=payload or {})

            elif action in {"sentinel.webhook_save", "sentinel_webhook_save"}:
                webhook_url = str(params.get("webhook_url") or "").strip()
                if not webhook_url:
                    raise RuntimeError("sentinel_webhook_missing")
                settings = cfg.get("sentinel_settings") if isinstance(cfg.get("sentinel_settings"), dict) else {}
                settings["webhook_url"] = webhook_url
                settings["updated_at"] = utc_now()
                cfg["sentinel_settings"] = settings
                cfg["updated_at"] = utc_now()
                ok, err = write_json(CONFIG_PATH, cfg, mode=0o600)
                if not ok:
                    raise RuntimeError(str(err or "config_write_failed"))
                payload = sentinels_status(webhook_url=webhook_url)
                result.update(ok=True, message="Sentinel Webhook gespeichert.", data=payload or {})

            elif action in {"sentinel.enable", "sentinel.disable", "sentinel.set_enabled", "sentinel_set_enabled"}:
                enabled = params.get("enabled")
                if action in {"sentinel.enable"}:
                    enabled = True
                elif action in {"sentinel.disable"}:
                    enabled = False

                if not isinstance(enabled, bool):
                    raise RuntimeError("sentinel_enabled_bool_required")

                profile = cfg.get("network_security") if isinstance(cfg.get("network_security"), dict) else {}
                profile["enabled"] = enabled
                profile["updated_at"] = utc_now()
                cfg["network_security"] = profile
                cfg["updated_at"] = utc_now()
                ok, err = write_json(CONFIG_PATH, cfg, mode=0o600)
                if not ok:
                    raise RuntimeError(str(err or "config_write_failed"))
                result.update(ok=True, message=("Sentinel-Sicherheitsmodus aktiviert." if enabled else "Sentinel-Sicherheitsmodus deaktiviert."), data={"enabled": enabled})

            else:
                result.update(ok=False, message=f"unknown_action:{action}")

        except NetControlError as exc:
            result.update(ok=False, message=exc.message or exc.code, data={"code": exc.code, "detail": exc.detail or ""})
        except SentinelManagerError as exc:
            result.update(ok=False, message=exc.message or exc.code, data={"code": exc.code, "detail": exc.detail or ""})
        except Exception as exc:
            result.update(ok=False, message=str(exc) or "action_failed")

        result["triggered_by"] = triggered_by
        result["at"] = utc_now()
        results.append(result)

    return results


def _pull_config_from_admin(cfg: dict, dev: dict) -> tuple[bool, dict, str]:
    base = _safe_base_url(cfg.get("admin_base_url", ""))
    if not base:
        return False, {}, "admin_base_url_missing"

    url = f"{base}/api/device/link/sync-config"
    payload = {
        "deviceUuid": dev.get("device_uuid") or "",
        "authKey": dev.get("auth_key") or "",
    }
    keys = cfg.get("panel_api_keys") if isinstance(cfg.get("panel_api_keys"), dict) else {}
    raspi_to_admin = str((keys.get("raspi_to_admin") if isinstance(keys, dict) else "") or "").strip()
    if raspi_to_admin:
        payload["apiKey"] = raspi_to_admin

    code, resp, err = http_post_json(url, payload, timeout=10)
    if code is None:
        return False, {}, str(err or "sync_config_pull_failed")
    if code < 200 or code >= 300 or not isinstance(resp, dict):
        detail = "sync_config_pull_failed"
        if isinstance(resp, dict):
            detail = str(resp.get("message") or resp.get("error") or detail)
        return False, {}, detail

    data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
    return True, data, ""


def _push_report_to_admin(cfg: dict, dev: dict, report: dict) -> tuple[bool, dict, str]:
    base = _safe_base_url(cfg.get("admin_base_url", ""))
    if not base:
        return False, {}, "admin_base_url_missing"

    url = f"{base}/api/device/link/sync-report"
    payload = {
        "deviceUuid": dev.get("device_uuid") or "",
        "authKey": dev.get("auth_key") or "",
        "sync_report": report,
    }
    keys = cfg.get("panel_api_keys") if isinstance(cfg.get("panel_api_keys"), dict) else {}
    raspi_to_admin = str((keys.get("raspi_to_admin") if isinstance(keys, dict) else "") or "").strip()
    if raspi_to_admin:
        payload["apiKey"] = raspi_to_admin

    code, resp, err = http_post_json(url, payload, timeout=12)
    if code is None:
        return False, {}, str(err or "sync_report_push_failed")
    if code < 200 or code >= 300 or not isinstance(resp, dict):
        detail = "sync_report_push_failed"
        if isinstance(resp, dict):
            detail = str(resp.get("message") or resp.get("error") or detail)
        return False, {}, detail

    return True, resp, ""


@bp_sync.get("/api/sync/status")
def api_sync_status():
    cfg = ensure_config()
    state = _ensure_sync_state(cfg)
    return jsonify(ok=True, data=state)


@bp_sync.get("/api/sync/fields")
def api_sync_fields():
    cfg = ensure_config()
    state = _ensure_sync_state(cfg)
    return jsonify(ok=True, data={"rules": state.get("rules") or []})


@bp_sync.post("/api/sync/overlay/apply")
def api_sync_overlay_apply():
    cfg = ensure_config()
    dev = ensure_device()
    data = request.get_json(force=True, silent=True) or {}

    if not is_authenticated():
        auth_ok, auth_error = _can_run_without_session(data, cfg, dev)
        if not auth_ok:
            return jsonify(ok=False, message="Unauthorized", error=auth_error), 401

    params = data.get("params") if isinstance(data.get("params"), dict) else data
    ok, payload = _apply_overlay_state_from_admin(params if isinstance(params, dict) else {})
    if not ok:
        return jsonify(ok=False, message="Overlay apply failed.", error=str(payload.get("error") or "overlay_apply_failed"), data=payload), 400

    return jsonify(ok=True, message="Overlay-Konfiguration angewendet.", data=payload)


@bp_sync.post("/api/sync/pull-config")
def api_sync_pull_config():
    cfg = ensure_config()
    dev = ensure_device()
    state = _ensure_sync_state(cfg)

    ok, data, error = _pull_config_from_admin(cfg, dev)
    if not ok:
        state["last_error"] = error
        state["last_sync_status"] = "error"
        state["last_sync_message"] = error
        cfg["updated_at"] = utc_now()
        write_json(CONFIG_PATH, cfg, mode=0o600)
        return jsonify(ok=False, message=error), 502

    profile = data.get("profile") if isinstance(data.get("profile"), dict) else {}
    rules = _normalize_rules(data.get("rules"))
    admin_values = data.get("adminValues") if isinstance(data.get("adminValues"), dict) else {}

    state["enabled"] = bool(profile.get("isEnabled", False))
    state["profile"] = profile
    state["rules"] = rules
    state["last_pull_at"] = utc_now()
    state["last_error"] = ""
    state["last_sync_status"] = "pulled"
    state["last_sync_message"] = "Sync-Konfiguration aktualisiert."

    apply_result = _apply_admin_values_to_portal(cfg, state, admin_values, rules)

    cfg["updated_at"] = utc_now()
    write_json(CONFIG_PATH, cfg, mode=0o600)

    return jsonify(
        ok=True,
        message="Sync-Konfiguration vom Admin geladen.",
        data={
            "profile": profile,
            "rules": rules,
            "applied_admin_values": apply_result,
        },
    )


@bp_sync.post("/api/sync/run")
def api_sync_run():
    cfg = ensure_config()
    dev = ensure_device()
    state = _ensure_sync_state(cfg)
    data = request.get_json(force=True, silent=True) or {}

    # Allow session-authenticated local operator OR authenticated remote admin call.
    if not is_authenticated():
        auth_ok, auth_error = _can_run_without_session(data, cfg, dev)
        if not auth_ok:
            return jsonify(ok=False, message="Unauthorized", error=auth_error), 401

    force_pull = _bool(data.get("pullConfig") or data.get("pull_config") or True, default=True)
    direction = str(data.get("direction") or "bidirectional").strip().lower()
    triggered_by = str(data.get("triggeredBy") or data.get("triggered_by") or "portal").strip() or "portal"
    actions_only = _bool(data.get("actionsOnly") or data.get("actions_only"), default=False)
    raw_actions = data.get("actions")
    requested_actions: list[dict] = []
    if isinstance(raw_actions, dict):
        requested_actions = [raw_actions]
    elif isinstance(raw_actions, list):
        requested_actions = [item for item in raw_actions if isinstance(item, dict)]

    if requested_actions and actions_only:
        action_results = _run_admin_actions(cfg, requested_actions, triggered_by)
        ok_all = all(bool(item.get("ok")) for item in action_results) if action_results else False

        state["last_sync_at"] = utc_now()
        state["last_sync_direction"] = "admin_to_portal"
        state["last_sync_triggered_by"] = triggered_by
        state["last_sync_status"] = "success" if ok_all else "partial_error"
        state["last_sync_message"] = "Aktionen ausgeführt." if ok_all else "Mindestens eine Aktion ist fehlgeschlagen."
        state["last_error"] = "" if ok_all else "admin_actions_partial_error"
        cfg["updated_at"] = utc_now()
        write_json(CONFIG_PATH, cfg, mode=0o600)

        return jsonify(
            ok=ok_all,
            message=state["last_sync_message"],
            data={
                "action_results": action_results,
                "sync_state": state,
            },
        ), (200 if ok_all else 502)

    if force_pull or not isinstance(state.get("profile"), dict) or not state.get("profile"):
        ok, pull_data, error = _pull_config_from_admin(cfg, dev)
        if not ok:
            state["last_sync_status"] = "error"
            state["last_sync_message"] = error
            state["last_error"] = error
            state["last_sync_at"] = utc_now()
            cfg["updated_at"] = utc_now()
            write_json(CONFIG_PATH, cfg, mode=0o600)
            return jsonify(ok=False, message=error, error="sync_pull_failed"), 502

        profile = pull_data.get("profile") if isinstance(pull_data.get("profile"), dict) else {}
        rules = _normalize_rules(pull_data.get("rules"))
        admin_values = pull_data.get("adminValues") if isinstance(pull_data.get("adminValues"), dict) else {}
        state["enabled"] = bool(profile.get("isEnabled", False))
        state["profile"] = profile
        state["rules"] = rules
        state["last_pull_at"] = utc_now()
        state["last_error"] = ""
        _apply_admin_values_to_portal(cfg, state, admin_values, rules)

    profile = state.get("profile") if isinstance(state.get("profile"), dict) else {}
    rules = _normalize_rules(state.get("rules"))

    if not bool(profile.get("isEnabled", False)):
        state["last_sync_status"] = "skipped"
        state["last_sync_message"] = "Sync-Profil deaktiviert."
        state["last_sync_at"] = utc_now()
        cfg["updated_at"] = utc_now()
        write_json(CONFIG_PATH, cfg, mode=0o600)
        return jsonify(ok=False, message="Sync-Profil ist deaktiviert."), 403

    if direction in {"portal_to_admin", "bidirectional"}:
        if not bool(profile.get("allowManualSyncFromPortal", False)) and not bool(profile.get("allowAutoSyncFromPortal", False)):
            state["last_sync_status"] = "skipped"
            state["last_sync_message"] = "Portal-Sync nicht freigegeben."
            state["last_sync_at"] = utc_now()
            cfg["updated_at"] = utc_now()
            write_json(CONFIG_PATH, cfg, mode=0o600)
            return jsonify(ok=False, message="Portal-Sync ist nicht freigegeben."), 403

    field_updates = _build_portal_field_updates(cfg, dev)
    filtered_updates = {}
    for key, value in field_updates.items():
        if _allow_direction(rules, key, "portal_to_admin"):
            filtered_updates[key] = value

    report = {
        "direction": direction,
        "triggered_by": triggered_by,
        "started_at": utc_now(),
        "field_updates": filtered_updates,
        "portal_state": {
            "mode": "play" if str(cfg.get("selected_stream_slug") or "").strip() else "setup",
            "selected_stream_slug": str(cfg.get("selected_stream_slug") or ""),
        },
        "applied": list(filtered_updates.keys()),
        "skipped": [key for key in field_updates.keys() if key not in filtered_updates],
    }

    if requested_actions:
        report["admin_actions"] = _run_admin_actions(cfg, requested_actions, triggered_by)

    ok_push, push_resp, push_error = _push_report_to_admin(cfg, dev, report)
    state["last_sync_at"] = utc_now()
    state["last_sync_direction"] = direction
    state["last_sync_triggered_by"] = triggered_by

    if not ok_push:
        state["last_sync_status"] = "error"
        state["last_sync_message"] = push_error
        state["last_error"] = push_error
        cfg["updated_at"] = utc_now()
        write_json(CONFIG_PATH, cfg, mode=0o600)
        return jsonify(ok=False, message=push_error, error="sync_push_failed", data={"report": report}), 502

    state["last_sync_status"] = "success"
    state["last_sync_message"] = str(push_resp.get("message") or "Sync erfolgreich.")
    state["last_error"] = ""
    state["last_push_at"] = utc_now()
    cfg["updated_at"] = utc_now()
    write_json(CONFIG_PATH, cfg, mode=0o600)

    return jsonify(
        ok=True,
        message=state["last_sync_message"],
        data={
            "report": report,
            "admin_response": push_resp,
            "sync_state": state,
        },
    )
