from __future__ import annotations

from flask import Blueprint, jsonify

from app.core.config import ensure_config
from app.core.netcontrol import NetControlError, spotify_connect_service_action

bp_spotify_connect = Blueprint("spotify_connect", __name__)


def _service_env_from_cfg() -> dict:
    cfg = ensure_config()
    return {
        "service_name": str(cfg.get("spotify_connect_service_name") or "").strip(),
        "service_user": str(cfg.get("spotify_connect_service_user") or "").strip(),
        "service_scope": str(cfg.get("spotify_connect_service_scope") or "").strip(),
        "service_candidates": str(cfg.get("spotify_connect_service_candidates") or "").strip(),
    }


def _ok(data: dict, status: int = 200):
    message = data.get("message") if isinstance(data, dict) else ""
    return jsonify(ok=True, success=True, message=message or "ok", data=data, error_code=""), status


def _error(code: str, message: str, status: int = 400, detail: str = ""):
    payload = {"code": code, "message": message}
    if detail:
        payload["detail"] = detail
    return jsonify(ok=False, success=False, message=message, data={}, error_code=code, error=payload), status


@bp_spotify_connect.get("/api/spotify-connect/status")
def api_spotify_connect_status():
    try:
        cfg = _service_env_from_cfg()
        data = spotify_connect_service_action("status", **cfg)
        return _ok(data)
    except NetControlError as exc:
        status = 500 if exc.code in ("execution_failed", "script_missing") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail or "")


@bp_spotify_connect.post("/api/spotify-connect/start")
def api_spotify_connect_start():
    try:
        cfg = _service_env_from_cfg()
        data = spotify_connect_service_action("start", **cfg)
        return _ok(data)
    except NetControlError as exc:
        status = 500 if exc.code in ("execution_failed", "script_missing") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail or "")


@bp_spotify_connect.post("/api/spotify-connect/stop")
def api_spotify_connect_stop():
    try:
        cfg = _service_env_from_cfg()
        data = spotify_connect_service_action("stop", **cfg)
        return _ok(data)
    except NetControlError as exc:
        status = 500 if exc.code in ("execution_failed", "script_missing") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail or "")


@bp_spotify_connect.post("/api/spotify-connect/restart")
def api_spotify_connect_restart():
    try:
        cfg = _service_env_from_cfg()
        data = spotify_connect_service_action("restart", **cfg)
        return _ok(data)
    except NetControlError as exc:
        status = 500 if exc.code in ("execution_failed", "script_missing") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail or "")


@bp_spotify_connect.post("/api/spotify-connect/refresh")
def api_spotify_connect_refresh():
    try:
        cfg = _service_env_from_cfg()
        data = spotify_connect_service_action("refresh", **cfg)
        return _ok(data)
    except NetControlError as exc:
        status = 500 if exc.code in ("execution_failed", "script_missing") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail or "")
