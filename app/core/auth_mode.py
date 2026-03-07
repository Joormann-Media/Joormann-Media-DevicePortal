from __future__ import annotations

from typing import Any


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
