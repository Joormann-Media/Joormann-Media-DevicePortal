from __future__ import annotations

from typing import Any

from app.core.netcontrol import NetControlError, bluetooth_audio_scan, bluetooth_audio_action, bluetooth_audio_devices, get_bluetooth_status


def scan(duration_seconds: int = 8) -> dict[str, Any]:
    seconds = max(4, min(30, int(duration_seconds or 8)))
    return bluetooth_audio_scan(scan_seconds=seconds)


def list_devices() -> dict[str, Any]:
    return bluetooth_audio_devices()


def status() -> dict[str, Any]:
    return get_bluetooth_status()


def pair(device_id: str) -> dict[str, Any]:
    return bluetooth_audio_action("pair", device_id=device_id)


def connect(device_id: str) -> dict[str, Any]:
    return bluetooth_audio_action("connect", device_id=device_id)


def disconnect(device_id: str) -> dict[str, Any]:
    return bluetooth_audio_action("disconnect", device_id=device_id)


def remove(device_id: str) -> dict[str, Any]:
    return bluetooth_audio_action("forget", device_id=device_id)


__all__ = [
    "scan",
    "list_devices",
    "status",
    "pair",
    "connect",
    "disconnect",
    "remove",
    "NetControlError",
]
