from __future__ import annotations

from typing import Any

from app.core.netcontrol import NetControlError, audio_outputs_status
from app.core.timeutil import utc_now
from app.services import bluetooth_service
from app.services.raspotify_service import status as raspotify_status
from app.services.radio_service import radio_service
from app.services.tts_service import tts_service


def collect_status(service_name: str = "") -> dict[str, Any]:
    errors: list[dict[str, str]] = []

    bluetooth: dict[str, Any] = {"ok": False}
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
        rsp = raspotify_status(service_name)
        raspotify = {"ok": True, **rsp}
    except NetControlError as exc:
        errors.append({"scope": "raspotify", "message": exc.message})
        raspotify = {"ok": False, "error": exc.message, "detail": exc.detail}

    radio = radio_service.status()
    tts = tts_service.status()

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
        "active_source": active_source,
        "updated_at": utc_now(),
        "errors": errors,
    }
