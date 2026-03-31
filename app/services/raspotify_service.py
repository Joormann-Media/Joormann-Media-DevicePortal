from __future__ import annotations

from typing import Any

from app.core.netcontrol import NetControlError, spotify_connect_service_action


def status(service_name: str = "") -> dict[str, Any]:
    return spotify_connect_service_action("status", service_name)


def start(service_name: str = "") -> dict[str, Any]:
    return spotify_connect_service_action("start", service_name)


def stop(service_name: str = "") -> dict[str, Any]:
    return spotify_connect_service_action("stop", service_name)


def restart(service_name: str = "") -> dict[str, Any]:
    return spotify_connect_service_action("restart", service_name)


__all__ = ["status", "start", "stop", "restart", "NetControlError"]
