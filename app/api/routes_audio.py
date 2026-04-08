from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.core.config import ensure_config
from app.core.jsonio import write_json
from app.core.netcontrol import (
    NetControlError,
    audio_output_set,
    audio_outputs_status,
    audio_volume_set,
    audio_sources_status,
    audio_source_volume_set,
)
from app.core.paths import CONFIG_PATH
from app.core.timeutil import utc_now
from app.services.audio_service import collect_status
from app.services.audio_backend import request_audio_backend
from app.services import bluetooth_service
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
    include_bt_raw = str(request.args.get("include_bluetooth") or "").strip().lower()
    include_bluetooth = include_bt_raw in {"1", "true", "yes", "on"}
    data = collect_status(**cfg, include_bluetooth=include_bluetooth)
    return _ok(data)


@bp_audio.get("/api/audio/mixer")
def api_audio_mixer():
    cfg = ensure_config()
    profile = _mixer_cfg(cfg)
    output_profile = cfg.get("audio_output") if isinstance(cfg.get("audio_output"), dict) else {}
    output_cache = output_profile.get("last_detected") if isinstance(output_profile.get("last_detected"), dict) else {}
    outputs = {}
    sources = {"ok": False, "backend": "", "default_source": "", "sources": [], "microphones": []}
    warnings: list[dict] = []
    try:
        outputs = audio_outputs_status()
        available = outputs.get("available_outputs") if isinstance(outputs.get("available_outputs"), list) else []
        has_available = any(bool(item.get("available")) for item in available if isinstance(item, dict))
        if not has_available and isinstance(output_cache, dict):
            cached_available = output_cache.get("available_outputs") if isinstance(output_cache.get("available_outputs"), list) else []
            if cached_available:
                outputs["available_outputs"] = cached_available
                if not str(outputs.get("current_output") or "").strip():
                    outputs["current_output"] = str(output_cache.get("current_output") or output_profile.get("selected_output") or "").strip()
                warnings.append({
                    "scope": "audio_outputs",
                    "code": "using_cached_outputs",
                    "message": "Live-Erkennung leer, verwende gecachte Audio-Outputs.",
                    "detail": "",
                })
        else:
            output_profile["last_detected"] = {
                "current_output": str(outputs.get("current_output") or "").strip(),
                "available_outputs": available,
                "updated_at": utc_now(),
            }
            cfg["audio_output"] = output_profile
            cfg["updated_at"] = utc_now()
            write_json(CONFIG_PATH, cfg, mode=0o600)

        if not str(outputs.get("current_output") or "").strip():
            outputs["current_output"] = str(output_profile.get("selected_output") or "").strip()
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed", "timeout") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)
    try:
        sources = audio_sources_status()
    except NetControlError as exc:
        warnings.append({
            "scope": "audio_sources",
            "code": exc.code,
            "message": exc.message,
            "detail": exc.detail,
        })
    backend_settings: dict[str, Any] | None = None
    backend_info: dict[str, Any] = {"ok": False}
    b_status, b_payload, b_err, b_base = request_audio_backend("GET", "/api/audio/mixer/settings", timeout=3.0)
    if b_status is not None and int(b_status) < 400 and isinstance(b_payload, dict) and bool(b_payload.get("ok", True)):
        raw_backend_settings = b_payload.get("data") if isinstance(b_payload.get("data"), dict) else b_payload
        if isinstance(raw_backend_settings, dict):
            backend_settings = raw_backend_settings
            backend_info = {"ok": True, "base_url": b_base}
    elif b_status is not None:
        backend_info = {"ok": False, "base_url": b_base, "status": int(b_status)}
    elif b_err:
        backend_info = {"ok": False, "error": b_err}

    view_settings = backend_settings if isinstance(backend_settings, dict) else {
        "tts_volume_percent": int(profile.get("tts_volume_percent") or 90),
        "tts_target_mode": str(profile.get("tts_target_mode") or "current"),
        "tts_target_output_id": str(profile.get("tts_target_output_id") or ""),
        "ducking_enabled": bool(profile.get("ducking_enabled", True)),
        "ducking_level_percent": int(profile.get("ducking_level_percent") or 30),
        "ducking_attack_ms": int(profile.get("ducking_attack_ms") or 120),
        "ducking_release_ms": int(profile.get("ducking_release_ms") or 450),
        "channel_volumes": profile.get("channel_volumes") if isinstance(profile.get("channel_volumes"), dict) else {},
        "mic_volumes": profile.get("mic_volumes") if isinstance(profile.get("mic_volumes"), dict) else {},
        "updated_at": str(profile.get("updated_at") or ""),
    }

    return _ok({
        "outputs": outputs,
        "sources": sources,
        "warnings": warnings,
        "settings": {
            **view_settings,
            "master_volume_percent": int(profile.get("master_volume_percent") or 65),
        },
        "audio_backend": backend_info,
    })


@bp_audio.post("/api/audio/mixer/settings")
def api_audio_mixer_settings_save():
    cfg = ensure_config()
    profile = _mixer_cfg(cfg)
    data = request.get_json(force=True, silent=True) or {}

    def _clamp(name: str, default: int, min_value: int, max_value: int) -> int:
        raw = data.get(name, profile.get(name, default))
        try:
            value = int(raw)
        except Exception:
            value = default
        return max(min_value, min(max_value, value))

    profile["master_volume_percent"] = _clamp("master_volume_percent", 65, 0, 150)
    profile["tts_volume_percent"] = _clamp("tts_volume_percent", 90, 0, 150)
    tts_target_mode = str(data.get("tts_target_mode", profile.get("tts_target_mode", "current")) or "current").strip().lower()
    if tts_target_mode not in ("current", "specific", "all"):
        tts_target_mode = "current"
    profile["tts_target_mode"] = tts_target_mode
    profile["tts_target_output_id"] = str(data.get("tts_target_output_id", profile.get("tts_target_output_id", "")) or "").strip()
    profile["ducking_level_percent"] = _clamp("ducking_level_percent", 30, 0, 100)
    profile["ducking_attack_ms"] = _clamp("ducking_attack_ms", 120, 0, 10000)
    profile["ducking_release_ms"] = _clamp("ducking_release_ms", 450, 0, 30000)
    if "ducking_enabled" in data:
        profile["ducking_enabled"] = bool(data.get("ducking_enabled"))
    else:
        profile["ducking_enabled"] = bool(profile.get("ducking_enabled", True))
    profile["updated_at"] = utc_now()

    ok_backend, backend_result, backend_status = _forward_audio_backend("POST", "/api/audio/mixer/settings", {
        "tts_volume_percent": profile["tts_volume_percent"],
        "ducking_enabled": profile["ducking_enabled"],
        "ducking_level_percent": profile["ducking_level_percent"],
        "ducking_attack_ms": profile["ducking_attack_ms"],
        "ducking_release_ms": profile["ducking_release_ms"],
    }, timeout=6.0)
    if not ok_backend:
        return _error(
            str(backend_result.get("error_code") or "audio_backend_failed"),
            str(backend_result.get("message") or "Audio-Backend nicht erreichbar"),
            status=backend_status,
            detail=str(backend_result.get("detail") or ""),
        )

    cfg["audio_mixer"] = profile
    cfg["updated_at"] = utc_now()
    ok, err = write_json(CONFIG_PATH, cfg, mode=0o600)
    if not ok:
        return _error("config_write_failed", "Could not persist audio mixer settings", status=500, detail=err)
    return _ok({"settings": profile, "audio_backend": backend_result})


def _outputs_to_sinks() -> dict:
    outputs = audio_outputs_status()
    sinks: list[dict] = []
    default_sink = None
    current_output = str(outputs.get("current_output") or "").strip()
    available = outputs.get("available_outputs") if isinstance(outputs.get("available_outputs"), list) else []
    for item in available:
        if not isinstance(item, dict):
            continue
        sink_name = str(item.get("sink_name") or "").strip()
        if not sink_name:
            continue
        sink = {
            "name": sink_name,
            "description": str(item.get("label") or item.get("description") or sink_name),
            "output_id": str(item.get("id") or ""),
            "is_default": str(item.get("id") or "") == current_output,
            "volume_percent": item.get("volume_percent"),
        }
        if sink["is_default"]:
            default_sink = sink_name
        sinks.append(sink)
    return {"ok": True, "default_sink": default_sink, "sinks": sinks, "outputs": outputs}


def _resolve_sink_name_for_output_id(output_id: str) -> str:
    outputs = audio_outputs_status()
    available = outputs.get("available_outputs") if isinstance(outputs.get("available_outputs"), list) else []
    target = str(output_id or "").strip()
    if not target:
        return ""
    for item in available:
        if not isinstance(item, dict):
            continue
        if str(item.get("id") or "").strip() != target:
            continue
        sink_name = str(item.get("sink_name") or "").strip()
        if sink_name:
            return sink_name
    return ""


def _resolve_output_id_for_sink(sink_name: str) -> str:
    outputs = audio_outputs_status()
    available = outputs.get("available_outputs") if isinstance(outputs.get("available_outputs"), list) else []
    for item in available:
        if not isinstance(item, dict):
            continue
        if str(item.get("sink_name") or "").strip() == sink_name:
            return str(item.get("id") or "")
    return ""


def _mixer_cfg(cfg: dict) -> dict:
    profile = cfg.get("audio_mixer") if isinstance(cfg.get("audio_mixer"), dict) else {}
    if not isinstance(profile.get("channel_volumes"), dict):
        profile["channel_volumes"] = {}
    if not isinstance(profile.get("mic_volumes"), dict):
        profile["mic_volumes"] = {}
    return profile


def _resolve_radio_output_id(request_data: dict | None = None) -> str:
    data = request_data if isinstance(request_data, dict) else {}
    explicit = str(data.get("output") or data.get("output_id") or "").strip()
    if explicit:
        return explicit
    cfg = ensure_config()
    profile = cfg.get("audio_output") if isinstance(cfg.get("audio_output"), dict) else {}
    return str(profile.get("selected_output") or "").strip()


def _forward_audio_backend(method: str, path: str, payload: dict | None = None, timeout: float = 8.0) -> tuple[bool, dict, int]:
    status, data, err, base_url = request_audio_backend(method, path, payload=payload or {}, timeout=timeout)
    if status is None:
        message = "Kein externer Jarvis-AudioPlayer erreichbar."
        if err:
            message = f"{message} ({err})"
        return False, {
            "ok": False,
            "error_code": "audio_backend_unavailable",
            "message": message,
            "backend_base_url": base_url,
        }, 502

    response_payload = data if isinstance(data, dict) else {}
    if status >= 400 or not bool(response_payload.get("ok", True)):
        message = str(response_payload.get("message") or f"Audio-Backend-Request fehlgeschlagen (HTTP {status}).").strip()
        return False, {
            "ok": False,
            "error_code": str(response_payload.get("error_code") or response_payload.get("error") or "audio_backend_failed"),
            "message": message,
            "detail": response_payload.get("detail") or "",
            "backend_base_url": base_url,
            "backend_response": response_payload,
        }, status

    backend_data = response_payload.get("data") if isinstance(response_payload.get("data"), dict) else response_payload
    return True, {
        "ok": True,
        "message": str(response_payload.get("message") or "ok"),
        "backend_base_url": base_url,
        **(backend_data if isinstance(backend_data, dict) else {}),
    }, 200


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


@bp_audio.get("/api/audio/sinks")
def api_audio_sinks():
    try:
        payload = _outputs_to_sinks()
        return _ok(payload)
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed", "timeout") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_audio.post("/api/audio/sink/select")
def api_audio_sink_select():
    data = request.get_json(force=True, silent=True) or {}
    sink_name = str(data.get("sink_name") or data.get("name") or "").strip()
    if not sink_name:
        return _error("invalid_payload", "Field 'sink_name' is required", status=400)
    try:
        output_id = _resolve_output_id_for_sink(sink_name) or sink_name
        payload = audio_output_set(output_id)
        payload["selected_sink"] = sink_name
        return _ok(payload)
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed", "timeout") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_audio.post("/api/audio/default")
def api_audio_default_set():
    data = request.get_json(force=True, silent=True) or {}
    sink_name = str(data.get("sink_name") or data.get("name") or "").strip()
    if not sink_name:
        return _error("invalid_payload", "Field 'name' is required", status=400)
    try:
        output_id = _resolve_output_id_for_sink(sink_name) or sink_name
        payload = audio_output_set(output_id)
        payload["selected_sink"] = sink_name
        return _ok(payload)
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed", "timeout") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


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
    if not stream_url:
        return _error("invalid_payload", "Field 'stream_url' is required", status=400)
    output_id = _resolve_radio_output_id(data)
    output_warning = ""
    if output_id:
        try:
            audio_output_set(output_id)
        except NetControlError as exc:
            output_warning = f"{exc.code}: {exc.message}"
    ok, result, status = _forward_audio_backend("POST", "/api/audio/radio/start", {"stream_url": stream_url, "streamUrl": stream_url, "url": stream_url})
    if not ok:
        return _error(str(result.get("error_code") or "radio_failed"), str(result.get("message") or "Radio-Start fehlgeschlagen"), status=status, detail=str(result.get("detail") or ""))
    if output_id:
        result["output_id"] = output_id
    if output_warning:
        result["output_warning"] = output_warning
    return _ok(result)


@bp_audio.post("/api/audio/radio/start")
def api_audio_radio_start():
    data = request.get_json(force=True, silent=True) or {}
    stream_url = str(data.get("stream_url") or data.get("url") or data.get("streamUrl") or "").strip()
    if not stream_url:
        return _error("invalid_payload", "Field 'stream_url' is required", status=400)
    output_id = _resolve_radio_output_id(data)
    output_warning = ""
    if output_id:
        try:
            audio_output_set(output_id)
        except NetControlError as exc:
            output_warning = f"{exc.code}: {exc.message}"
    ok, result, status = _forward_audio_backend("POST", "/api/audio/radio/start", {"stream_url": stream_url, "streamUrl": stream_url, "url": stream_url})
    if not ok:
        return _error(str(result.get("error_code") or "radio_failed"), str(result.get("message") or "Radio-Start fehlgeschlagen"), status=status, detail=str(result.get("detail") or ""))
    if output_id:
        result["output_id"] = output_id
    if output_warning:
        result["output_warning"] = output_warning
    return _ok(result)


@bp_audio.post("/api/audio/radio/stop")
def api_audio_radio_stop():
    ok, result, status = _forward_audio_backend("POST", "/api/audio/radio/stop", {})
    if not ok:
        return _error(str(result.get("error_code") or "radio_stop_failed"), str(result.get("message") or "Radio-Stop fehlgeschlagen"), status=status, detail=str(result.get("detail") or ""))
    return _ok(result)


@bp_audio.get("/api/audio/radio/status")
def api_audio_radio_status():
    ok, result, status = _forward_audio_backend("GET", "/api/audio/status", timeout=5.0)
    if not ok:
        return _error(str(result.get("error_code") or "radio_status_failed"), str(result.get("message") or "Radio-Status nicht verfügbar"), status=status, detail=str(result.get("detail") or ""))
    radio = result.get("radio") if isinstance(result.get("radio"), dict) else {}
    payload = {
        "running": bool(radio.get("running")),
        "stream_url": str(radio.get("stream_url") or "").strip() or None,
        "playback_url": str(radio.get("playback_url") or "").strip() or None,
        "stream_kind": str(radio.get("stream_kind") or "unknown"),
        "pid": radio.get("pid"),
        "last_error": radio.get("last_error"),
        "rtsp_adapter": radio.get("rtsp_adapter") if isinstance(radio.get("rtsp_adapter"), dict) else {},
        "backend_base_url": result.get("backend_base_url"),
    }
    return _ok(payload)


@bp_audio.post("/api/radio/play")
def api_radio_play_alias():
    return api_audio_radio_play()


@bp_audio.post("/api/radio/start")
def api_radio_start_alias():
    return api_audio_radio_start()


@bp_audio.post("/api/radio/stop")
def api_radio_stop_alias():
    return api_audio_radio_stop()


@bp_audio.get("/api/radio/status")
def api_radio_status_alias():
    return api_audio_radio_status()


@bp_audio.post("/api/audio/stop-all")
def api_audio_stop_all():
    ok, result, status = _forward_audio_backend("POST", "/api/audio/radio/stop", {})
    if not ok:
        return _error(str(result.get("error_code") or "stop_all_failed"), str(result.get("message") or "Stop-All fehlgeschlagen"), status=status, detail=str(result.get("detail") or ""))
    tts_ok, tts_result, _tts_status = _forward_audio_backend("POST", "/api/audio/tts/stop", {})
    return _ok({"radio": result, "tts": tts_result if tts_ok else {"ok": False}, "message": "all sources stopped"})


@bp_audio.post("/api/audio/tts/test")
def api_audio_tts_test():
    data = request.get_json(force=True, silent=True) or {}
    text = str(data.get("text") or "Dies ist ein TTS Test.").strip() or "Dies ist ein TTS Test."
    ok, result, status = _forward_audio_backend("POST", "/api/audio/tts/speak", {"text": text, "message": text}, timeout=10.0)
    if not ok:
        return _error(str(result.get("error_code") or "tts_failed"), str(result.get("message") or "TTS fehlgeschlagen"), status=status, detail=str(result.get("detail") or ""))
    return _ok(result)


@bp_audio.post("/api/audio/tts/speak")
def api_audio_tts_speak():
    data = request.get_json(force=True, silent=True) or {}
    text = str(data.get("text") or data.get("message") or "").strip()
    if not text:
        return _error("invalid_payload", "Field 'text' is required", status=400)
    ok, result, status = _forward_audio_backend("POST", "/api/audio/tts/speak", {"text": text, "message": text}, timeout=10.0)
    if not ok:
        return _error(str(result.get("error_code") or "tts_failed"), str(result.get("message") or "TTS fehlgeschlagen"), status=status, detail=str(result.get("detail") or ""))
    return _ok(result)


@bp_audio.post("/api/audio/volume")
def api_audio_volume():
    data = request.get_json(force=True, silent=True) or {}
    raw_volume = data.get("volume_percent", data.get("volume", data.get("percent", 100)))
    try:
        volume = int(raw_volume)
    except Exception:
        return _error("invalid_payload", "Field 'volume' must be numeric", status=400)
    sink_name = str(data.get("sink_name") or data.get("name") or "").strip()
    try:
        payload = audio_volume_set(sink_name or None, volume)
        cfg = ensure_config()
        profile = _mixer_cfg(cfg)
        profile["master_volume_percent"] = int(payload.get("volume_percent") or volume)
        if sink_name:
            channel_volumes = profile.get("channel_volumes") if isinstance(profile.get("channel_volumes"), dict) else {}
            channel_volumes[sink_name] = volume
            profile["channel_volumes"] = channel_volumes
        profile["updated_at"] = utc_now()
        cfg["audio_mixer"] = profile
        cfg["updated_at"] = utc_now()
        write_json(CONFIG_PATH, cfg, mode=0o600)
        return _ok(payload)
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed", "timeout") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_audio.post("/api/audio/sink/volume")
def api_audio_sink_volume():
    return api_audio_volume()


@bp_audio.post("/api/audio/default/volume")
def api_audio_default_volume():
    return api_audio_volume()


@bp_audio.post("/api/audio/channel/volume")
def api_audio_channel_volume():
    data = request.get_json(force=True, silent=True) or {}
    channel_id = str(data.get("channel_id") or data.get("output_id") or "").strip()
    raw_volume = data.get("volume_percent", data.get("volume", data.get("percent", 100)))
    if not channel_id:
        return _error("invalid_payload", "Field 'channel_id' is required", status=400)
    try:
        volume = int(raw_volume)
    except Exception:
        return _error("invalid_payload", "Field 'volume' must be numeric", status=400)
    try:
        sink_name = _resolve_sink_name_for_output_id(channel_id)
        target = sink_name or channel_id
        payload = audio_volume_set(target, volume)
        cfg = ensure_config()
        profile = _mixer_cfg(cfg)
        channel_volumes = profile.get("channel_volumes") if isinstance(profile.get("channel_volumes"), dict) else {}
        channel_volumes[channel_id] = int(payload.get("volume_percent") or volume)
        profile["channel_volumes"] = channel_volumes
        profile["updated_at"] = utc_now()
        cfg["audio_mixer"] = profile
        cfg["updated_at"] = utc_now()
        write_json(CONFIG_PATH, cfg, mode=0o600)
        payload["channel_id"] = channel_id
        payload["sink_name"] = sink_name
        payload["target"] = target
        return _ok(payload)
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed", "timeout") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)


@bp_audio.post("/api/audio/mic/volume")
def api_audio_mic_volume():
    data = request.get_json(force=True, silent=True) or {}
    source_name = str(data.get("source_name") or data.get("mic_id") or "").strip()
    raw_volume = data.get("volume_percent", data.get("volume", data.get("percent", 100)))
    if not source_name:
        return _error("invalid_payload", "Field 'source_name' is required", status=400)
    try:
        volume = int(raw_volume)
    except Exception:
        return _error("invalid_payload", "Field 'volume' must be numeric", status=400)
    try:
        payload = audio_source_volume_set(source_name, volume)
        cfg = ensure_config()
        profile = _mixer_cfg(cfg)
        mic_volumes = profile.get("mic_volumes") if isinstance(profile.get("mic_volumes"), dict) else {}
        mic_volumes[source_name] = int(payload.get("volume_percent") or volume)
        profile["mic_volumes"] = mic_volumes
        profile["updated_at"] = utc_now()
        cfg["audio_mixer"] = profile
        cfg["updated_at"] = utc_now()
        write_json(CONFIG_PATH, cfg, mode=0o600)
        return _ok(payload)
    except NetControlError as exc:
        status = 500 if exc.code in ("script_missing", "execution_failed", "timeout") else 400
        return _error(exc.code, exc.message, status=status, detail=exc.detail)
