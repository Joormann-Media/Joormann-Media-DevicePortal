from __future__ import annotations

from flask import session

from app.core.timeutil import utc_now

SESSION_KEY = "portal_auth"
PENDING_PANEL_2FA_KEY = "portal_auth_panel_2fa_pending"


def current_session() -> dict:
    data = session.get(SESSION_KEY)
    return data if isinstance(data, dict) else {}


def is_authenticated() -> bool:
    data = current_session()
    return bool(data.get("authenticated")) and bool(str(data.get("username") or "").strip())


def login_user(*, username: str, mode: str, display_name: str = "", user_id: int | None = None) -> dict:
    payload = {
        "authenticated": True,
        "username": username.strip(),
        "display_name": (display_name or "").strip(),
        "mode": (mode or "local_system").strip() or "local_system",
        "user_id": int(user_id) if isinstance(user_id, int) and user_id > 0 else None,
        "logged_in_at": utc_now(),
    }
    session[SESSION_KEY] = payload
    session.permanent = True
    return payload


def logout_user() -> None:
    session.pop(SESSION_KEY, None)
    clear_pending_panel_2fa()


def set_pending_panel_2fa(payload: dict) -> dict:
    data = payload if isinstance(payload, dict) else {}
    session[PENDING_PANEL_2FA_KEY] = data
    session.permanent = True
    return data


def get_pending_panel_2fa() -> dict:
    value = session.get(PENDING_PANEL_2FA_KEY)
    return value if isinstance(value, dict) else {}


def has_pending_panel_2fa() -> bool:
    data = get_pending_panel_2fa()
    return bool(data) and bool(str(data.get("base_url") or "").strip())


def clear_pending_panel_2fa() -> None:
    session.pop(PENDING_PANEL_2FA_KEY, None)
