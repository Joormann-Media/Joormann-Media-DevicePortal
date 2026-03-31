from __future__ import annotations

from typing import Any

from app.core.netcontrol import NetControlError, spotify_connect_service_action


def status(
    service_name: str = "",
    service_user: str = "",
    service_scope: str = "",
    service_candidates: str = "",
) -> dict[str, Any]:
    return spotify_connect_service_action(
        "status",
        service_name,
        service_user=service_user,
        service_scope=service_scope,
        service_candidates=service_candidates,
    )


def start(
    service_name: str = "",
    service_user: str = "",
    service_scope: str = "",
    service_candidates: str = "",
) -> dict[str, Any]:
    return spotify_connect_service_action(
        "start",
        service_name,
        service_user=service_user,
        service_scope=service_scope,
        service_candidates=service_candidates,
    )


def stop(
    service_name: str = "",
    service_user: str = "",
    service_scope: str = "",
    service_candidates: str = "",
) -> dict[str, Any]:
    return spotify_connect_service_action(
        "stop",
        service_name,
        service_user=service_user,
        service_scope=service_scope,
        service_candidates=service_candidates,
    )


def restart(
    service_name: str = "",
    service_user: str = "",
    service_scope: str = "",
    service_candidates: str = "",
) -> dict[str, Any]:
    return spotify_connect_service_action(
        "restart",
        service_name,
        service_user=service_user,
        service_scope=service_scope,
        service_candidates=service_candidates,
    )


__all__ = ["status", "start", "stop", "restart", "NetControlError"]
