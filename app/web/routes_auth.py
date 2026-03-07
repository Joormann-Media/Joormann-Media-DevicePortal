from __future__ import annotations

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

from app.core.auth_local import LocalAuthError, authenticate_local_user, list_interactive_users
from app.core.auth_mode import resolve_auth_mode
from app.core.auth_panel import PanelAuthError, authenticate_via_panel, complete_panel_two_factor
from app.core.auth_session import (
    clear_pending_panel_2fa,
    current_session,
    get_pending_panel_2fa,
    has_pending_panel_2fa,
    is_authenticated,
    login_user,
    logout_user,
    set_pending_panel_2fa,
)
from app.core.config import ensure_config
from app.core.device import ensure_device

bp_auth = Blueprint("auth", __name__)


def _safe_next(value: str) -> str:
    target = (value or "").strip()
    if not target.startswith("/"):
        return "/"
    if target.startswith("//"):
        return "/"
    return target


def _mode_human(mode: str) -> str:
    return "Login über Adminpanel" if mode == "panel_remote" else "Lokaler System-Login"


@bp_auth.get("/login")
def login_page():
    if is_authenticated():
        return redirect(_safe_next(request.args.get("next") or "/"))

    cfg = ensure_config()
    mode_info = resolve_auth_mode(cfg)
    pending_2fa = get_pending_panel_2fa() if mode_info["mode"] == "panel_remote" else {}
    return render_template(
        "login.html",
        auth_mode=mode_info["mode"],
        auth_mode_label=_mode_human(mode_info["mode"]),
        auth_mode_reason=mode_info.get("reason", ""),
        panel_base_url=mode_info.get("panel_base_url", ""),
        linked_user_count=len(mode_info.get("linked_user_ids", [])),
        error_message="",
        next_url=_safe_next(request.args.get("next") or "/"),
        twofa_required=bool(pending_2fa),
        twofa_user=(pending_2fa.get("display_name") or pending_2fa.get("username") or "") if isinstance(pending_2fa, dict) else "",
    )


@bp_auth.post("/login")
def login_submit():
    if is_authenticated():
        return redirect(_safe_next(request.form.get("next") or request.args.get("next") or "/"))

    cfg = ensure_config()
    dev = ensure_device()
    mode_info = resolve_auth_mode(cfg)

    username = (request.form.get("username") or request.form.get("_username") or "").strip()
    password = request.form.get("password") or request.form.get("_password") or ""
    next_url = _safe_next(request.form.get("next") or request.args.get("next") or "/")

    if not username or not password:
        return render_template(
            "login.html",
            auth_mode=mode_info["mode"],
            auth_mode_label=_mode_human(mode_info["mode"]),
            auth_mode_reason=mode_info.get("reason", ""),
            panel_base_url=mode_info.get("panel_base_url", ""),
            linked_user_count=len(mode_info.get("linked_user_ids", [])),
            error_message="Bitte Benutzername und Passwort eingeben.",
            next_url=next_url,
            twofa_required=False,
            twofa_user="",
        ), 400

    try:
        if mode_info["mode"] == "panel_remote":
            clear_pending_panel_2fa()
            result = authenticate_via_panel(
                base_url=str(mode_info.get("panel_base_url") or ""),
                device_uuid=str(dev.get("device_uuid") or ""),
                auth_key=str(dev.get("auth_key") or ""),
                username=username,
                password=password,
                allowed_user_ids=list(mode_info.get("linked_user_ids") or []),
            )
            if bool(result.get("requires_2fa")):
                pending_2fa = result.get("pending_2fa") if isinstance(result.get("pending_2fa"), dict) else {}
                pending_2fa["username"] = str(result.get("username") or username)
                pending_2fa["display_name"] = str(result.get("display_name") or result.get("username") or username)
                pending_2fa["user_id"] = int(result.get("user_id") or 0) or None
                set_pending_panel_2fa(pending_2fa)
                return render_template(
                    "login.html",
                    auth_mode=mode_info["mode"],
                    auth_mode_label=_mode_human(mode_info["mode"]),
                    auth_mode_reason=mode_info.get("reason", ""),
                    panel_base_url=mode_info.get("panel_base_url", ""),
                    linked_user_count=len(mode_info.get("linked_user_ids", [])),
                    error_message="",
                    next_url=next_url,
                    twofa_required=True,
                    twofa_user=str(pending_2fa.get("display_name") or pending_2fa.get("username") or username),
                ), 200
            login_user(
                username=str(result.get("username") or username),
                mode="panel_remote",
                display_name=str(result.get("display_name") or username),
                user_id=int(result.get("user_id") or 0) or None,
            )
        else:
            result = authenticate_local_user(username=username, password=password)
            login_user(
                username=str(result.get("username") or username),
                mode="local_system",
                display_name=str(result.get("display_name") or username),
                user_id=int(result.get("uid") or 0) or None,
            )
    except (LocalAuthError, PanelAuthError) as exc:
        return render_template(
            "login.html",
            auth_mode=mode_info["mode"],
            auth_mode_label=_mode_human(mode_info["mode"]),
            auth_mode_reason=mode_info.get("reason", ""),
            panel_base_url=mode_info.get("panel_base_url", ""),
            linked_user_count=len(mode_info.get("linked_user_ids", [])),
            error_message=str(exc),
            next_url=next_url,
            twofa_required=has_pending_panel_2fa(),
            twofa_user="",
        ), 401

    return redirect(next_url)


@bp_auth.post("/login/2fa")
def login_submit_2fa():
    if is_authenticated():
        return redirect(_safe_next(request.form.get("next") or request.args.get("next") or "/"))

    cfg = ensure_config()
    mode_info = resolve_auth_mode(cfg)
    next_url = _safe_next(request.form.get("next") or request.args.get("next") or "/")
    if mode_info["mode"] != "panel_remote":
        return redirect(url_for("auth.login_page", next=next_url))

    pending_2fa = get_pending_panel_2fa()
    if not pending_2fa:
        return render_template(
            "login.html",
            auth_mode=mode_info["mode"],
            auth_mode_label=_mode_human(mode_info["mode"]),
            auth_mode_reason=mode_info.get("reason", ""),
            panel_base_url=mode_info.get("panel_base_url", ""),
            linked_user_count=len(mode_info.get("linked_user_ids", [])),
            error_message="2FA-Session abgelaufen. Bitte erneut einloggen.",
            next_url=next_url,
            twofa_required=False,
            twofa_user="",
        ), 401

    code = (request.form.get("otp_code") or request.form.get("_auth_code") or "").strip()
    try:
        complete_panel_two_factor(pending_2fa=pending_2fa, code=code)
    except PanelAuthError as exc:
        return render_template(
            "login.html",
            auth_mode=mode_info["mode"],
            auth_mode_label=_mode_human(mode_info["mode"]),
            auth_mode_reason=mode_info.get("reason", ""),
            panel_base_url=mode_info.get("panel_base_url", ""),
            linked_user_count=len(mode_info.get("linked_user_ids", [])),
            error_message=str(exc),
            next_url=next_url,
            twofa_required=True,
            twofa_user=str(pending_2fa.get("display_name") or pending_2fa.get("username") or ""),
        ), 401

    login_user(
        username=str(pending_2fa.get("username") or ""),
        mode="panel_remote",
        display_name=str(pending_2fa.get("display_name") or pending_2fa.get("username") or ""),
        user_id=int(pending_2fa.get("user_id") or 0) or None,
    )
    clear_pending_panel_2fa()
    return redirect(next_url)


@bp_auth.post("/logout")
def logout_submit():
    logout_user()
    session.clear()
    return redirect(url_for("auth.login_page"))


@bp_auth.get("/api/auth/mode")
def api_auth_mode():
    cfg = ensure_config()
    mode_info = resolve_auth_mode(cfg)
    return jsonify(
        ok=True,
        mode=mode_info["mode"],
        mode_label=_mode_human(mode_info["mode"]),
        reason=mode_info.get("reason", ""),
        panel_linked=bool(mode_info.get("panel_linked")),
        linked_user_ids=list(mode_info.get("linked_user_ids") or []),
        linked_user_count=len(mode_info.get("linked_user_ids") or []),
        panel_base_url=str(mode_info.get("panel_base_url") or ""),
    )


@bp_auth.get("/api/auth/status")
def api_auth_status():
    cfg = ensure_config()
    mode_info = resolve_auth_mode(cfg)
    auth = current_session()
    return jsonify(
        ok=True,
        authenticated=is_authenticated(),
        auth=auth,
        mode=mode_info["mode"],
        mode_label=_mode_human(mode_info["mode"]),
    )


@bp_auth.get("/api/auth/local-users")
def api_auth_local_users():
    cfg = ensure_config()
    mode_info = resolve_auth_mode(cfg)
    if mode_info["mode"] != "local_system":
        return jsonify(ok=True, users=[], count=0, mode=mode_info["mode"])
    users = list_interactive_users()
    return jsonify(ok=True, users=users, count=len(users), mode=mode_info["mode"])
