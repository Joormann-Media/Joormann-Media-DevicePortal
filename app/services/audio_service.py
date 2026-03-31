from __future__ import annotations

from typing import Any

from app.core.netcontrol import NetControlError, audio_outputs_status
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
            "volume_percent": None,
        }
        if sink["is_default"]:
            default_sink = sink_name
        sinks.append(sink)

    return {"default_sink": default_sink, "sinks": sinks}


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
