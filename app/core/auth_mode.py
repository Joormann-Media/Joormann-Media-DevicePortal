from __future__ import annotations

from typing import Any

import requests

from app.core.jsonio import write_json
from app.core.paths import CONFIG_PATH
from app.core.timeutil import utc_now


def _safe_base_url(value: Any) -> str:
    base = str(value or "").strip()
    return base.rstrip("/")


def _extract_ids_from_payload(payload: dict | None) -> tuple[list[int], list[int]]:
    if not isinstance(payload, dict):
        return ([], [])
    candidates: list[dict] = [payload]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.append(data)

    users: set[int] = set()
    customers: set[int] = set()

    def _add(value: Any, bucket: set[int]) -> None:
        iv = _normalize_int(value)
        if iv > 0:
            bucket.add(iv)

    def _consume_list(value: Any, bucket: set[int]) -> None:
        if not isinstance(value, list):
            return
        for row in value:
            if isinstance(row, dict):
                _add(row.get("id"), bucket)
                _add(row.get("user_id"), bucket)
                _add(row.get("customer_id"), bucket)
            else:
                _add(row, bucket)

    for source in candidates:
        _add(source.get("linkedUserId"), users)
        _add(source.get("linked_user_id"), users)
        _add(source.get("linkedCustomerId"), customers)
        _add(source.get("linked_customer_id"), customers)
        _consume_list(source.get("linkedUsers"), users)
        _consume_list(source.get("linked_users"), users)
        _consume_list(source.get("users"), users)
        _consume_list(source.get("linkedCustomers"), customers)
        _consume_list(source.get("linked_customers"), customers)
        _consume_list(source.get("customers"), customers)

    return (sorted(users), sorted(customers))


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

    user_ids, customer_ids = _extract_ids_from_payload(body if isinstance(body, dict) else None)
    if not user_ids and not customer_ids:
        return False

    cfg["panel_linked_users"] = [{"id": uid} for uid in user_ids if int(uid) > 0]
    cfg["panel_linked_customers"] = [{"id": cid} for cid in customer_ids if int(cid) > 0]
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


def resolve_auth_mode(cfg: dict) -> dict:
    panel_state = cfg.get("panel_link_state") if isinstance(cfg.get("panel_link_state"), dict) else {}
    linked = bool(panel_state.get("linked"))
    base = str(cfg.get("admin_base_url") or "").strip()
    user_ids = linked_user_ids_from_config(cfg)

    if linked and base and user_ids:
        return {
            "mode": "panel_remote",
            "reason": "panel_linked_with_user_links",
            "panel_linked": True,
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
