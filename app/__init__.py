from __future__ import annotations

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

from app.api.routes_network import bp_network
from app.api.routes_panel import bp_panel
from app.api.routes_plan import bp_plan
from app.api.routes_status import bp_status
from app.core.config import ensure_config
from app.core.device import ensure_device
from app.core.fingerprint import ensure_fingerprint
from app.web.routes_ui import bp_ui


def create_app() -> Flask:
    app = Flask(__name__, template_folder='templates', static_folder='static')

    ensure_config()
    ensure_device()
    ensure_fingerprint()

    app.register_blueprint(bp_status)
    app.register_blueprint(bp_panel)
    app.register_blueprint(bp_plan)
    app.register_blueprint(bp_network)
    app.register_blueprint(bp_ui)

    @app.errorhandler(Exception)
    def handle_exception(exc: Exception):
        if isinstance(exc, HTTPException):
            return (
                jsonify(ok=False, error=exc.name.lower().replace(" ", "_"), detail=exc.description),
                exc.code or 500,
            )
        return jsonify(ok=False, error='internal_error', detail=str(exc)), 500

    return app
