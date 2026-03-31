from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.core.config import ensure_config
from app.core.jsonio import write_json
from app.core.netcontrol import NetControlError, spotify_connect_install, spotify_connect_service_action
from app.core.paths import CONFIG_PATH
from pathlib import Path
from app.core.timeutil import utc_now

bp_spotify_connect = Blueprint("spotify_connect", __name__)


def _service_env_from_cfg() -> dict:
    cfg = ensure_config()
    return {
        "service_name": str(cfg.get("spotify_connect_service_name") or "").strip(),
        "service_user": str(cfg.get("spotify_connect_service_user") or "").strip(),
        "service_scope": str(cfg.get("spotify_connect_service_scope") or "").strip(),
        "service_candidates": str(cfg.get("spotify_connect_service_candidates") or "").strip(),
    }


def _update_raspotify_conf(device_name: str) -> None:
    if not device_name:
        return
    conf_dir = Path.home() / ".config" / "raspotify"
    conf_dir.mkdir(parents=True, exist_ok=True)
    conf_path = conf_dir / "conf"
    existing: dict[str, str] = {}
    if conf_path.exists():
        for line in conf_path.read_text().splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            existing[key.strip()] = value.strip().strip('"').strip("'")
    existing["DEVICE_NAME"] = device_name
    # Ensure OPTIONS contains --name override while keeping other options.
    opts = existing.get("OPTIONS", "")
    if "--name" in opts:
        opts = " ".join([part for part in opts.split() if part != "--name" and not part.startswith("--name=")])
    opts = f'{opts} --name "{device_name}"'.strip()
    existing["OPTIONS"] = opts
    lines = [f'{k}="{v}"' for k, v in existing.items()]
    conf_path.write_text("\n".join(lines) + "\n")


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


@bp_spotify_connect.post("/api/spotify-connect/enable")
def api_spotify_connect_enable():
    try:
        cfg = _service_env_from_cfg()
        data = spotify_connect_service_action("enable", **cfg)
        return _ok(data)
    except NetControlError as exc:
        status = 500 if exc.code in ("execution_failed", "script_missing") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail or "")


@bp_spotify_connect.post("/api/spotify-connect/disable")
def api_spotify_connect_disable():
    try:
        cfg = _service_env_from_cfg()
        data = spotify_connect_service_action("disable", **cfg)
        return _ok(data)
    except NetControlError as exc:
        status = 500 if exc.code in ("execution_failed", "script_missing") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail or "")


@bp_spotify_connect.post("/api/spotify-connect/install")
def api_spotify_connect_install():
    try:
        cfg = _service_env_from_cfg()
        data = spotify_connect_install(**cfg)
        return _ok(data)
    except NetControlError as exc:
        status = 500 if exc.code in ("execution_failed", "script_missing") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail or "")


@bp_spotify_connect.get("/api/spotify-connect/config")
def api_spotify_connect_config_get():
    cfg = ensure_config()
    return _ok(
        {
            "service_name": str(cfg.get("spotify_connect_service_name") or "").strip(),
            "service_user": str(cfg.get("spotify_connect_service_user") or "").strip(),
            "service_scope": str(cfg.get("spotify_connect_service_scope") or "").strip() or "auto",
            "service_candidates": str(cfg.get("spotify_connect_service_candidates") or "").strip(),
            "device_name": str(cfg.get("spotify_connect_device_name") or "").strip(),
        }
    )


@bp_spotify_connect.post("/api/spotify-connect/config")
def api_spotify_connect_config_set():
    cfg = ensure_config()
    data = request.get_json(force=True, silent=True) or {}
    cfg["spotify_connect_service_name"] = str(data.get("service_name") or "").strip()
    cfg["spotify_connect_service_user"] = str(data.get("service_user") or "").strip()
    cfg["spotify_connect_service_scope"] = str(data.get("service_scope") or "").strip()
    cfg["spotify_connect_service_candidates"] = str(data.get("service_candidates") or "").strip()
    cfg["spotify_connect_device_name"] = str(data.get("device_name") or "").strip()
    cfg["updated_at"] = utc_now()
    _update_raspotify_conf(cfg.get("spotify_connect_device_name") or "")
    ok, write_err = write_json(CONFIG_PATH, cfg, mode=0o600)
    if not ok:
        return _error("config_write_failed", "Could not persist spotify connect config", status=500, detail=write_err)
    return _ok(
        {
            "service_name": str(cfg.get("spotify_connect_service_name") or "").strip(),
            "service_user": str(cfg.get("spotify_connect_service_user") or "").strip(),
            "service_scope": str(cfg.get("spotify_connect_service_scope") or "").strip(),
            "service_candidates": str(cfg.get("spotify_connect_service_candidates") or "").strip(),
            "device_name": str(cfg.get("spotify_connect_device_name") or "").strip(),
        }
    )
