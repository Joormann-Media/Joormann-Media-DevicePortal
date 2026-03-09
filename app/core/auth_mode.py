from __future__ import annotations

from typing import Any

import requests

from app.core.jsonio import write_json
from app.core.paths import CONFIG_PATH
from app.core.timeutil import utc_now


def _safe_base_url(value: Any) -> str:
    base = str(value or "").strip()
    return base.rstrip("/")


def _extract_ids_from_payload(payload: dict | None) -> tuple[list[dict], list[dict]]:
    if not isinstance(payload, dict):
        return ([], [])
    candidates: list[dict] = [payload]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.append(data)

    users_by_id: dict[int, dict] = {}
    customers_by_id: dict[int, dict] = {}

    def _add(value: Any, bucket: dict[int, dict]) -> int:
        iv = _normalize_int(value)
        if iv > 0:
            bucket.setdefault(iv, {"id": iv})
        return iv

    def _consume_list(value: Any, bucket: dict[int, dict], item_type: str) -> None:
        if not isinstance(value, list):
            return
        for row in value:
            if isinstance(row, dict):
                if item_type == "user":
                    rid = _add(row.get("id") or row.get("user_id"), bucket)
                    if rid > 0:
                        current = bucket.get(rid, {"id": rid})
                        for key in ("username", "email", "displayName", "display_name", "avatar", "avatarUrl", "avatar_url", "userDir", "user_dir"):
                            if row.get(key) not in (None, ""):
                                current[key] = row.get(key)
                        bucket[rid] = current
                else:
                    _add(row.get("id") or row.get("customer_id"), bucket)
            else:
                _add(row, bucket)

    for source in candidates:
        _add(source.get("linkedUserId"), users_by_id)
        _add(source.get("linked_user_id"), users_by_id)
        _add(source.get("linkedCustomerId"), customers_by_id)
        _add(source.get("linked_customer_id"), customers_by_id)
        _consume_list(source.get("linkedUsers"), users_by_id, "user")
        _consume_list(source.get("linked_users"), users_by_id, "user")
        _consume_list(source.get("users"), users_by_id, "user")
        _consume_list(source.get("linkedCustomers"), customers_by_id, "customer")
        _consume_list(source.get("linked_customers"), customers_by_id, "customer")
        _consume_list(source.get("customers"), customers_by_id, "customer")

    users = [users_by_id[k] for k in sorted(users_by_id.keys())]
    customers = [customers_by_id[k] for k in sorted(customers_by_id.keys())]
    return (users, customers)


def refresh_link_targets_from_panel(cfg: dict, dev: dict) -> bool:
    if not isinstance(cfg, dict) or not isinstance(dev, dict):
        return False
    panel_state = cfg.get("panel_link_state") if isinstance(cfg.get("panel_link_state"), dict) else {}
    if not bool(panel_state.get("linked")):
        return False

    base = _safe_base_url(cfg.get("admin_base_url"))
    device_uuid = str(dev.get("device_uuid") or "").strip()
    auth_key = str(dev.get("auth_key") or "").strip()
    if not base or not device_uuid or not auth_key:
        return False

    url = f"{base}/api/device/link/auth-context"
    payload = {"deviceUuid": device_uuid, "authKey": auth_key}
    try:
        response = requests.post(url, json=payload, timeout=6)
    except Exception:
        return False
    if response.status_code < 200 or response.status_code >= 300:
        return False

    try:
        body = response.json()
    except Exception:
        body = {}

    user_rows, customer_rows = _extract_ids_from_payload(body if isinstance(body, dict) else None)
    if not user_rows and not customer_rows:
        return False

    cfg["panel_linked_users"] = [row for row in user_rows if _normalize_int(row.get("id")) > 0]
    cfg["panel_linked_customers"] = [row for row in customer_rows if _normalize_int(row.get("id")) > 0]
    cfg["updated_at"] = utc_now()
    write_json(CONFIG_PATH, cfg, mode=0o600)
    return True


def _normalize_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def linked_user_ids_from_config(cfg: dict) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()

    def _push(value: Any) -> None:
        user_id = _normalize_int(value)
        if user_id > 0 and user_id not in seen:
            seen.add(user_id)
            ids.append(user_id)

    def _consume_list(value: Any) -> None:
        if not isinstance(value, list):
            return
        for item in value:
            if isinstance(item, dict):
                _push(item.get("id"))
                _push(item.get("user_id"))
            else:
                _push(item)

    _consume_list(cfg.get("panel_linked_users"))

    panel_state = cfg.get("panel_link_state") if isinstance(cfg.get("panel_link_state"), dict) else {}
    last_response = panel_state.get("last_response") if isinstance(panel_state.get("last_response"), dict) else {}
    candidates: list[dict] = []
    if isinstance(last_response, dict):
        candidates.append(last_response)
        nested = last_response.get("data")
        if isinstance(nested, dict):
            candidates.append(nested)

    for source in candidates:
        _push(source.get("linkedUserId"))
        _push(source.get("linked_user_id"))
        _push(source.get("userId"))
        _push(source.get("user_id"))
        _consume_list(source.get("linkedUsers"))
        _consume_list(source.get("linked_users"))
        _consume_list(source.get("users"))

    return ids


def resolve_auth_mode(cfg: dict, *, force_local: bool = False, force_reason: str = "") -> dict:
    panel_state = cfg.get("panel_link_state") if isinstance(cfg.get("panel_link_state"), dict) else {}
    linked = bool(panel_state.get("linked"))
    base = str(cfg.get("admin_base_url") or "").strip()
    user_ids = linked_user_ids_from_config(cfg)

    if linked and base and user_ids and not force_local:
        return {
            "mode": "panel_remote",
            "reason": "panel_linked_with_user_links",
            "panel_linked": True,
            "panel_base_url": base,
            "linked_user_ids": user_ids,
        }

    if force_local:
        reason = force_reason or "forced_local_mode"
        return {
            "mode": "local_system",
            "reason": reason,
            "panel_linked": linked,
            "panel_base_url": base,
            "linked_user_ids": user_ids,
        }

    if linked and base and not user_ids:
        reason = "panel_linked_without_user_links"
    elif linked and not base:
        reason = "panel_linked_without_base_url"
    else:
        reason = "panel_not_linked"

    return {
        "mode": "local_system",
        "reason": reason,
        "panel_linked": linked,
        "panel_base_url": base,
        "linked_user_ids": user_ids,
    }
