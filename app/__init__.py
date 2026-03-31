from __future__ import annotations

import os
from ipaddress import ip_address

from flask import Flask, jsonify, redirect, request, url_for
from werkzeug.exceptions import HTTPException

from app.api.routes_audio import bp_audio
from app.api.routes_network import bp_network
from app.api.routes_overlay import bp_overlay
from app.api.routes_panel import bp_panel
from app.api.routes_plan import bp_plan
from app.api.routes_spotify_connect import bp_spotify_connect
from app.api.routes_sync import bp_sync
from app.api.routes_status import bp_status
from app.api.routes_stream import bp_stream
from app.core.auth_mode import resolve_auth_mode
from app.core.auth_session import is_authenticated
from app.core.connectivity_mode import detect_connectivity_setup_mode
from app.core.connectivity_watchdog import start_connectivity_watchdog
from app.core.config import ensure_config
from app.core.device import ensure_device
from app.core.fingerprint import ensure_fingerprint
from app.web.routes_auth import bp_auth
from app.web.routes_ui import bp_ui


def _is_local_unauth_stream_sync() -> bool:
    if request.method != "POST" or request.path != "/api/stream/sync":
        return False
    remote = (request.remote_addr or "").strip()
    if not remote:
        return False
    try:
        return ip_address(remote).is_loopback
    except ValueError:
        return False


def create_app() -> Flask:
    app = Flask(__name__, template_folder='templates', static_folder='static')

    cfg = ensure_config()
    ensure_device()
    ensure_fingerprint()
    start_connectivity_watchdog()
    app.config["SECRET_KEY"] = (cfg.get("session_secret") or "").strip()
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = os.getenv("PORTAL_SESSION_COOKIE_SECURE", "0").strip().lower() in {"1", "true", "yes", "on"}

    app.register_blueprint(bp_status)
    app.register_blueprint(bp_panel)
    app.register_blueprint(bp_sync)
    app.register_blueprint(bp_plan)
    app.register_blueprint(bp_audio)
    app.register_blueprint(bp_network)
    app.register_blueprint(bp_spotify_connect)
    app.register_blueprint(bp_stream)
    app.register_blueprint(bp_overlay)
    app.register_blueprint(bp_auth)
    app.register_blueprint(bp_ui)

    @app.before_request
    def require_portal_login():
        path = request.path or "/"
        if request.method == "OPTIONS":
            return None

        public_exact = {
            "/health",
            "/login",
            "/login/2fa",
            "/logout",
            "/ap-display",
            "/api/auth/mode",
            "/api/auth/status",
            "/api/auth/local-users",
            "/api/panel/admin-sync-payload",
            "/api/sync/run",
            "/api/sync/overlay/apply",
        }
        if path in public_exact or path.startswith("/static/"):
            return None

        # Allow local service-driven stream sync even when panel-remote auth is active.
        if _is_local_unauth_stream_sync():
            return None

        if is_authenticated():
            return None

        setup_mode = detect_connectivity_setup_mode()
        mode_info = resolve_auth_mode(
            ensure_config(),
            force_local=bool(setup_mode.get("active")),
            force_reason="connectivity_setup_mode",
        )
        if path.startswith("/api/"):
            return (
                jsonify(
                    ok=False,
                    error="login_required",
                    detail="Bitte zuerst im DevicePortal einloggen.",
                    auth_mode=mode_info.get("mode", "local_system"),
                    auth_reason=mode_info.get("reason", ""),
                    connectivity_setup_mode=setup_mode,
                ),
                401,
            )

        next_url = request.full_path if request.query_string else request.path
        if next_url.endswith("?"):
            next_url = next_url[:-1]
        return redirect(url_for("auth.login_page", next=next_url))

    @app.errorhandler(Exception)
    def handle_exception(exc: Exception):
        if isinstance(exc, HTTPException):
            return (
                jsonify(ok=False, error=exc.name.lower().replace(" ", "_"), detail=exc.description),
                exc.code or 500,
            )
        return jsonify(ok=False, error='internal_error', detail=str(exc)), 500

    return app
