from __future__ import annotations

import hmac

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

bp_sync = Blueprint("sync", __name__)


def _bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


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
