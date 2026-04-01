from __future__ import annotations

from typing import Any

from app.core.netcontrol import NetControlError, audio_outputs_status, audio_output_set
from app.core.config import ensure_config
from app.core.jsonio import write_json
from app.core.paths import CONFIG_PATH
from app.core.timeutil import utc_now
from app.services import bluetooth_service
from app.services.raspotify_service import status as raspotify_status
from app.services.radio_service import radio_service
from app.services.tts_service import tts_service


def _build_sinks_payload(outputs: dict[str, Any]) -> dict[str, Any]:
    sinks: list[dict[str, Any]] = []
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
            "type": str(item.get("type") or ""),
            "is_default": str(item.get("id") or "") == current_output,
            "volume_percent": item.get("volume_percent"),
        }
        if sink["is_default"]:
            default_sink = sink_name
        sinks.append(sink)

    return {"default_sink": default_sink, "sinks": sinks}


def _pick_fallback_output(outputs: dict[str, Any]) -> str:
    available = outputs.get("available_outputs") if isinstance(outputs.get("available_outputs"), list) else []
    for preferred in ("local_speaker", "local_hdmi"):
        for item in available:
            if str(item.get("id") or "") == preferred and item.get("available"):
                return preferred
    for item in available:
        if item.get("available"):
            return str(item.get("id") or "")
    return ""


def _auto_restore_output(outputs: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(outputs, dict) or not outputs.get("ok"):
        return outputs
    current_output = str(outputs.get("current_output") or "").strip()
    if current_output:
        return outputs

    cfg = ensure_config()
    profile = cfg.get("audio_output") if isinstance(cfg.get("audio_output"), dict) else {}
    selected = str(profile.get("selected_output") or "").strip()
    available = outputs.get("available_outputs") if isinstance(outputs.get("available_outputs"), list) else []
    available_ids = {str(item.get("id") or "") for item in available if item.get("available")}

    target = ""
    if selected and selected in available_ids:
        target = selected
    else:
        target = _pick_fallback_output(outputs)

    if not target:
        return outputs

    try:
        audio_output_set(target)
        refreshed = audio_outputs_status()
        refreshed["ok"] = True
        if target != selected:
            profile["selected_output"] = target
            profile["updated_at"] = utc_now()
            cfg["audio_output"] = profile
            cfg["updated_at"] = utc_now()
            write_json(CONFIG_PATH, cfg, mode=0o600)
        return refreshed
    except NetControlError:
        return outputs


def collect_status(
    service_name: str = "",
    service_user: str = "",
    service_scope: str = "",
    service_candidates: str = "",
    include_bluetooth: bool = True,
) -> dict[str, Any]:
    errors: list[dict[str, str]] = []

    bluetooth: dict[str, Any] = {"ok": False}
    if include_bluetooth:
        try:
            bt_status = bluetooth_service.status()
            bt_devices = bluetooth_service.list_devices()
            devices = bt_devices.get("devices") if isinstance(bt_devices.get("devices"), list) else []
            connected = [d for d in devices if isinstance(d, dict) and d.get("connected")]
            bluetooth = {
                "ok": True,
                "status": bt_status,
                "devices": devices,
                "connected_count": len(connected),
            }
        except NetControlError as exc:
            errors.append({"scope": "bluetooth", "message": exc.message})
            bluetooth = {"ok": False, "error": exc.message, "detail": exc.detail}

    outputs: dict[str, Any] = {"ok": False}
    try:
        outputs = audio_outputs_status()
        outputs["ok"] = True
        outputs = _auto_restore_output(outputs)
    except NetControlError as exc:
        errors.append({"scope": "audio_outputs", "message": exc.message})
        outputs = {"ok": False, "error": exc.message, "detail": exc.detail}

    raspotify: dict[str, Any] = {"ok": False}
    try:
        rsp = raspotify_status(
            service_name=service_name,
            service_user=service_user,
            service_scope=service_scope,
            service_candidates=service_candidates,
        )
        raspotify = {"ok": True, **rsp}
    except NetControlError as exc:
        errors.append({"scope": "raspotify", "message": exc.message})
        raspotify = {"ok": False, "error": exc.message, "detail": exc.detail}

    radio = radio_service.status()
    tts = tts_service.status()

    sinks_payload = _build_sinks_payload(outputs if isinstance(outputs, dict) else {})

    active_source = "idle"
    if tts.get("running"):
        active_source = "tts"
    elif radio.get("running"):
        active_source = "radio"

    return {
        "bluetooth": bluetooth,
        "outputs": outputs,
        "raspotify": raspotify,
        "radio": radio,
        "tts": tts,
        "sources": {"radio": radio, "tts": tts},
        "sinks": sinks_payload,
        "active_source": active_source,
        "updated_at": utc_now(),
        "errors": errors,
    }
