from __future__ import annotations

from typing import Any


def _normalize_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def linked_user_ids_from_config(cfg: dict) -> list[int]:
    raw = cfg.get("panel_linked_users") if isinstance(cfg.get("panel_linked_users"), list) else []
    ids: list[int] = []
    seen: set[int] = set()
    for item in raw:
        user_id = 0
        if isinstance(item, dict):
            user_id = _normalize_int(item.get("id"))
        else:
            user_id = _normalize_int(item)
        if user_id > 0 and user_id not in seen:
            seen.add(user_id)
            ids.append(user_id)
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
