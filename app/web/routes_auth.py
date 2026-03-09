from __future__ import annotations

import ipaddress

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

from app.core.auth_local import LocalAuthError, authenticate_local_user, list_interactive_users
from app.core.auth_mode import refresh_link_targets_from_panel, resolve_auth_mode
from app.core.auth_panel import PanelAuthError, authenticate_via_panel, complete_panel_two_factor
from app.core.connectivity_mode import detect_connectivity_setup_mode
from app.core.netcontrol import NetControlError, get_ap_status
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


def _is_dev_mode() -> bool:
    raw = (request.args.get("mode") or request.form.get("mode") or request.form.get("dev_mode") or "").strip().lower()
    return raw in {"dev", "1", "true", "yes", "on"}


def _is_ap_request() -> bool:
    host = (request.host or "").split(":", 1)[0].strip()
    remote_addr = (request.remote_addr or "").strip()

    try:
        ap_status = get_ap_status()
    except NetControlError:
        ap_status = {}

    ap_ip = str(ap_status.get("ip") or "").strip()
    if ap_ip and host == ap_ip:
        return True

    if remote_addr:
        try:
            remote_ip = ipaddress.ip_address(remote_addr)
            if remote_ip in ipaddress.ip_network("192.168.4.0/24"):
                return True
        except ValueError:
            pass

    return False


def _is_local_display_request() -> bool:
    remote_addr = (request.remote_addr or "").strip()
    if not remote_addr:
        return False
    try:
        return ipaddress.ip_address(remote_addr).is_loopback
    except ValueError:
        return False


def _login_template_name() -> str:
    return "login_ap.html" if _is_ap_request() else "login.html"


def _force_local_auth_mode(setup_mode: dict) -> bool:
    return bool(setup_mode.get("active")) or _is_ap_request()


@bp_auth.get("/login")
def login_page():
    setup_mode = detect_connectivity_setup_mode()
    if bool(setup_mode.get("active")) and _is_local_display_request():
        return redirect(url_for("ui.ap_display"))

    if is_authenticated():
        next_url = _safe_next(request.args.get("next") or "/")
        if _is_ap_request() and next_url == "/":
            return redirect(url_for("ui.wifi_setup"))
        return redirect(next_url)

    cfg = ensure_config()
    dev = ensure_device()
    force_local_auth = _force_local_auth_mode(setup_mode)
    if not force_local_auth:
        refresh_link_targets_from_panel(cfg, dev)
    mode_info = resolve_auth_mode(
        cfg,
        force_local=force_local_auth,
        force_reason="ap_or_connectivity_setup_mode",
    )
    login_template = _login_template_name()
    dev_mode = _is_dev_mode()
    pending_2fa = get_pending_panel_2fa() if mode_info["mode"] == "panel_remote" else {}
    return render_template(
        login_template,
        auth_mode=mode_info["mode"],
        auth_mode_label=_mode_human(mode_info["mode"]),
        auth_mode_reason=mode_info.get("reason", ""),
        panel_base_url=mode_info.get("panel_base_url", ""),
        linked_user_count=len(mode_info.get("linked_user_ids", [])),
        error_message="",
        next_url=_safe_next(request.args.get("next") or "/"),
        twofa_required=bool(pending_2fa),
        twofa_user=(pending_2fa.get("display_name") or pending_2fa.get("username") or "") if isinstance(pending_2fa, dict) else "",
        dev_mode=dev_mode,
        connectivity_setup_mode=setup_mode,
    )


@bp_auth.post("/login")
def login_submit():
    if is_authenticated():
        return redirect(_safe_next(request.form.get("next") or request.args.get("next") or "/"))

    cfg = ensure_config()
    dev = ensure_device()
    setup_mode = detect_connectivity_setup_mode()
    force_local_auth = _force_local_auth_mode(setup_mode)
    if not force_local_auth:
        refresh_link_targets_from_panel(cfg, dev)
    mode_info = resolve_auth_mode(
        cfg,
        force_local=force_local_auth,
        force_reason="ap_or_connectivity_setup_mode",
    )
    login_template = _login_template_name()
    dev_mode = _is_dev_mode()

    username = (request.form.get("username") or request.form.get("_username") or "").strip()
    password = request.form.get("password") or request.form.get("_password") or ""
    next_url = _safe_next(request.form.get("next") or request.args.get("next") or "/")
    mode_override = (request.form.get("auth_mode_override") or "").strip()
    submit_mode = mode_override if mode_override in {"local_system", "panel_remote"} else mode_info["mode"]
    if force_local_auth:
        submit_mode = "local_system"

    if not username or not password:
        return render_template(
            login_template,
            auth_mode=mode_info["mode"],
            auth_mode_label=_mode_human(mode_info["mode"]),
            auth_mode_reason=mode_info.get("reason", ""),
            panel_base_url=mode_info.get("panel_base_url", ""),
            linked_user_count=len(mode_info.get("linked_user_ids", [])),
            error_message="Bitte Benutzername und Passwort eingeben.",
            next_url=next_url,
            twofa_required=False,
            twofa_user="",
            dev_mode=dev_mode,
            connectivity_setup_mode=setup_mode,
        ), 400

    try:
        if submit_mode == "panel_remote":
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
                    login_template,
                    auth_mode=mode_info["mode"],
                    auth_mode_label=_mode_human(mode_info["mode"]),
                    auth_mode_reason=mode_info.get("reason", ""),
                    panel_base_url=mode_info.get("panel_base_url", ""),
                    linked_user_count=len(mode_info.get("linked_user_ids", [])),
                    error_message="",
                    next_url=next_url,
                    twofa_required=True,
                    twofa_user=str(pending_2fa.get("display_name") or pending_2fa.get("username") or username),
                    dev_mode=dev_mode,
                    connectivity_setup_mode=setup_mode,
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
            login_template,
            auth_mode=mode_info["mode"],
            auth_mode_label=_mode_human(mode_info["mode"]),
            auth_mode_reason=mode_info.get("reason", ""),
            panel_base_url=mode_info.get("panel_base_url", ""),
            linked_user_count=len(mode_info.get("linked_user_ids", [])),
            error_message=str(exc),
            next_url=next_url,
            twofa_required=has_pending_panel_2fa(),
            twofa_user="",
            dev_mode=dev_mode,
            connectivity_setup_mode=setup_mode,
        ), 401

    if _is_ap_request() and next_url == "/":
        return redirect(url_for("ui.wifi_setup"))
    return redirect(next_url)


@bp_auth.post("/login/2fa")
def login_submit_2fa():
    if is_authenticated():
        return redirect(_safe_next(request.form.get("next") or request.args.get("next") or "/"))

    cfg = ensure_config()
    setup_mode = detect_connectivity_setup_mode()
    force_local_auth = _force_local_auth_mode(setup_mode)
    mode_info = resolve_auth_mode(
        cfg,
        force_local=force_local_auth,
        force_reason="ap_or_connectivity_setup_mode",
    )
    login_template = _login_template_name()
    dev_mode = _is_dev_mode()
    next_url = _safe_next(request.form.get("next") or request.args.get("next") or "/")
    if mode_info["mode"] != "panel_remote":
        if dev_mode:
            return redirect(url_for("auth.login_page", next=next_url, mode="dev"))
        return redirect(url_for("auth.login_page", next=next_url))

    pending_2fa = get_pending_panel_2fa()
    if not pending_2fa:
        return render_template(
            login_template,
            auth_mode=mode_info["mode"],
            auth_mode_label=_mode_human(mode_info["mode"]),
            auth_mode_reason=mode_info.get("reason", ""),
            panel_base_url=mode_info.get("panel_base_url", ""),
            linked_user_count=len(mode_info.get("linked_user_ids", [])),
            error_message="2FA-Session abgelaufen. Bitte erneut einloggen.",
            next_url=next_url,
            twofa_required=False,
            twofa_user="",
            dev_mode=dev_mode,
            connectivity_setup_mode=setup_mode,
        ), 401

    code = (request.form.get("otp_code") or request.form.get("_auth_code") or "").strip()
    try:
        complete_panel_two_factor(pending_2fa=pending_2fa, code=code)
    except PanelAuthError as exc:
        return render_template(
            login_template,
            auth_mode=mode_info["mode"],
            auth_mode_label=_mode_human(mode_info["mode"]),
            auth_mode_reason=mode_info.get("reason", ""),
            panel_base_url=mode_info.get("panel_base_url", ""),
            linked_user_count=len(mode_info.get("linked_user_ids", [])),
            error_message=str(exc),
            next_url=next_url,
            twofa_required=True,
            twofa_user=str(pending_2fa.get("display_name") or pending_2fa.get("username") or ""),
            dev_mode=dev_mode,
            connectivity_setup_mode=setup_mode,
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
    dev = ensure_device()
    setup_mode = detect_connectivity_setup_mode()
    force_local_auth = _force_local_auth_mode(setup_mode)
    if not force_local_auth:
        refresh_link_targets_from_panel(cfg, dev)
    mode_info = resolve_auth_mode(
        cfg,
        force_local=force_local_auth,
        force_reason="ap_or_connectivity_setup_mode",
    )
    return jsonify(
        ok=True,
        mode=mode_info["mode"],
        mode_label=_mode_human(mode_info["mode"]),
        reason=mode_info.get("reason", ""),
        panel_linked=bool(mode_info.get("panel_linked")),
        linked_user_ids=list(mode_info.get("linked_user_ids") or []),
        linked_user_count=len(mode_info.get("linked_user_ids") or []),
        panel_base_url=str(mode_info.get("panel_base_url") or ""),
        connectivity_setup_mode=setup_mode,
    )


@bp_auth.get("/api/auth/status")
def api_auth_status():
    cfg = ensure_config()
    dev = ensure_device()
    setup_mode = detect_connectivity_setup_mode()
    force_local_auth = _force_local_auth_mode(setup_mode)
    if not force_local_auth:
        refresh_link_targets_from_panel(cfg, dev)
    mode_info = resolve_auth_mode(
        cfg,
        force_local=force_local_auth,
        force_reason="ap_or_connectivity_setup_mode",
    )
    auth = current_session()
    return jsonify(
        ok=True,
        authenticated=is_authenticated(),
        auth=auth,
        mode=mode_info["mode"],
        mode_label=_mode_human(mode_info["mode"]),
        connectivity_setup_mode=setup_mode,
    )


@bp_auth.get("/api/auth/local-users")
def api_auth_local_users():
    cfg = ensure_config()
    setup_mode = detect_connectivity_setup_mode()
    force_local_auth = _force_local_auth_mode(setup_mode)
    mode_info = resolve_auth_mode(
        cfg,
        force_local=force_local_auth,
        force_reason="ap_or_connectivity_setup_mode",
    )
    if mode_info["mode"] != "local_system":
        return jsonify(ok=True, users=[], count=0, mode=mode_info["mode"], connectivity_setup_mode=setup_mode)
    users = list_interactive_users()
    return jsonify(ok=True, users=users, count=len(users), mode=mode_info["mode"], connectivity_setup_mode=setup_mode)
