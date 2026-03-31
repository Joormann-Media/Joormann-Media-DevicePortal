from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.core.config import ensure_config
from app.core.jsonio import write_json
from app.core.netcontrol import NetControlError, audio_output_set
from app.core.paths import CONFIG_PATH
from app.core.timeutil import utc_now
from app.services.audio_service import collect_status
from app.services import bluetooth_service
from app.services.radio_service import radio_service
from app.services.tts_service import tts_service
from app.services.raspotify_service import status as raspotify_status, start as raspotify_start, stop as raspotify_stop, restart as raspotify_restart

bp_audio = Blueprint("audio", __name__)


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


@bp_audio.get("/api/audio/status")
def api_audio_status():
    cfg = _service_env_from_cfg()
    data = collect_status(**cfg)
    return _ok(data)


@bp_audio.get("/api/audio/bluetooth/scan")
def api_audio_bluetooth_scan():
    raw_duration = request.args.get("duration", "8")
    try:
        duration = int(raw_duration)
    except Exception:
        return _error("invalid_payload", "Query param 'duration' must be int", status=400)
    try:
        payload = bluetooth_service.scan(duration)
        return _ok(payload)
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed", "timeout") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_audio.post("/api/audio/bluetooth/pair")
def api_audio_bluetooth_pair():
    data = request.get_json(force=True, silent=True) or {}
    device_id = str(data.get("device_id") or data.get("id") or data.get("mac") or "").strip()
    if not device_id:
        return _error("invalid_payload", "Field 'device_id' is required", status=400)
    try:
        payload = bluetooth_service.pair(device_id)
        return _ok(payload)
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed", "timeout") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_audio.post("/api/audio/bluetooth/connect")
def api_audio_bluetooth_connect():
    data = request.get_json(force=True, silent=True) or {}
    device_id = str(data.get("device_id") or data.get("id") or data.get("mac") or "").strip()
    if not device_id:
        return _error("invalid_payload", "Field 'device_id' is required", status=400)
    try:
        payload = bluetooth_service.connect(device_id)
        return _ok(payload)
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed", "timeout") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_audio.post("/api/audio/bluetooth/disconnect")
def api_audio_bluetooth_disconnect():
    data = request.get_json(force=True, silent=True) or {}
    device_id = str(data.get("device_id") or data.get("id") or data.get("mac") or "").strip()
    if not device_id:
        return _error("invalid_payload", "Field 'device_id' is required", status=400)
    try:
        payload = bluetooth_service.disconnect(device_id)
        return _ok(payload)
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed", "timeout") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_audio.post("/api/audio/bluetooth/remove")
def api_audio_bluetooth_remove():
    data = request.get_json(force=True, silent=True) or {}
    device_id = str(data.get("device_id") or data.get("id") or data.get("mac") or "").strip()
    if not device_id:
        return _error("invalid_payload", "Field 'device_id' is required", status=400)
    try:
        payload = bluetooth_service.remove(device_id)
        return _ok(payload)
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed", "timeout") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_audio.post("/api/audio/output/set")
def api_audio_output_set():
    data = request.get_json(force=True, silent=True) or {}
    output = str(data.get("output") or "").strip()
    if not output:
        return _error("invalid_payload", "Field 'output' is required", status=400)
    cfg = ensure_config()
    try:
        payload = audio_output_set(output)
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed", "timeout") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)

    profile = cfg.get("audio_output") if isinstance(cfg.get("audio_output"), dict) else {}
    profile["selected_output"] = output
    profile["updated_at"] = utc_now()
    cfg["audio_output"] = profile
    cfg["updated_at"] = utc_now()
    ok, err = write_json(CONFIG_PATH, cfg, mode=0o600)
    if not ok:
        return _error("config_write_failed", "Could not persist audio output selection", status=500, detail=err)
    payload["saved"] = profile
    return _ok(payload)


@bp_audio.get("/api/audio/raspotify/status")
def api_audio_raspotify_status():
    try:
        cfg = _service_env_from_cfg()
        data = raspotify_status(**cfg)
        return _ok(data)
    except NetControlError as exc:
        status = 500 if exc.code in ("execution_failed", "script_missing") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail or "")


@bp_audio.post("/api/audio/raspotify/start")
def api_audio_raspotify_start():
    try:
        cfg = _service_env_from_cfg()
        data = raspotify_start(**cfg)
        return _ok(data)
    except NetControlError as exc:
        status = 500 if exc.code in ("execution_failed", "script_missing") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail or "")


@bp_audio.post("/api/audio/raspotify/stop")
def api_audio_raspotify_stop():
    try:
        cfg = _service_env_from_cfg()
        data = raspotify_stop(**cfg)
        return _ok(data)
    except NetControlError as exc:
        status = 500 if exc.code in ("execution_failed", "script_missing") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail or "")


@bp_audio.post("/api/audio/raspotify/restart")
def api_audio_raspotify_restart():
    try:
        cfg = _service_env_from_cfg()
        data = raspotify_restart(**cfg)
        return _ok(data)
    except NetControlError as exc:
        status = 500 if exc.code in ("execution_failed", "script_missing") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail or "")


@bp_audio.post("/api/audio/radio/play")
def api_audio_radio_play():
    data = request.get_json(force=True, silent=True) or {}
    stream_url = str(data.get("stream_url") or data.get("url") or "").strip()
    result = radio_service.play(stream_url)
    if not result.get("ok"):
        return _error("radio_failed", result.get("message", "Radio-Start fehlgeschlagen"), status=400)
    return _ok(result)


@bp_audio.post("/api/audio/radio/stop")
def api_audio_radio_stop():
    result = radio_service.stop()
    return _ok(result)


@bp_audio.post("/api/audio/tts/test")
def api_audio_tts_test():
    data = request.get_json(force=True, silent=True) or {}
    text = str(data.get("text") or "Dies ist ein TTS Test.").strip() or "Dies ist ein TTS Test."
    result = tts_service.speak(text=text)
    if not result.get("ok"):
        return _error("tts_failed", result.get("message", "TTS fehlgeschlagen"), status=400)
    return _ok(result)
