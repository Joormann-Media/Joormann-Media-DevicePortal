from __future__ import annotations

import os
import threading
import time

from app.core.netcontrol import NetControlError, get_ap_status, get_network_info, set_ap_enabled, set_wifi_enabled
from app.core.network_events import log_event

_thread_lock = threading.Lock()
_thread_started = False


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if raw == "":
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if raw == "":
        return default
    try:
        value = int(raw)
    except Exception:
        return default
    return max(minimum, min(maximum, value))


def _has_uplink(info: dict) -> bool:
    interfaces = (info or {}).get("interfaces") or {}
    lan = interfaces.get("lan") or {}
    wifi = interfaces.get("wifi") or {}

    lan_up = bool(lan.get("carrier")) or bool((lan.get("ip") or "").strip())
    wifi_up = bool(wifi.get("connected"))
    return bool(lan_up or wifi_up)


def _run_check_once() -> None:
    info = get_network_info()
    if _has_uplink(info):
        return

    ap = get_ap_status()
    if bool(ap.get("active")):
        return

    # Ensure radio/interface is enabled before trying hotspot profile activation.
    try:
        set_wifi_enabled(True)
    except NetControlError:
        # Continue; set_ap_enabled may still recover depending on device state.
        pass

    set_ap_enabled(True)
    ap_after = get_ap_status()
    log_event(
        "ap",
        "AP auto-enabled by connectivity watchdog (missing LAN/WLAN uplink)",
        level="warning",
        data={
            "source": "connectivity_watchdog",
            "reason": "missing_lan_and_wifi_uplink",
            "ap_active": bool(ap_after.get("active")),
            "ap_ssid": ap_after.get("ssid", ""),
            "ap_ip": ap_after.get("ip", ""),
        },
    )


def _watchdog_loop(start_delay_seconds: int, interval_seconds: int, error_backoff_seconds: int) -> None:
    if start_delay_seconds > 0:
        time.sleep(start_delay_seconds)

    next_error_log_at = 0.0
    while True:
        try:
            _run_check_once()
        except NetControlError as exc:
            now = time.monotonic()
            if now >= next_error_log_at:
                log_event(
                    "ap",
                    "Connectivity watchdog check failed",
                    level="warning",
                    data={
                        "source": "connectivity_watchdog",
                        "code": exc.code,
                        "message": exc.message,
                        "detail": exc.detail,
                    },
                )
                next_error_log_at = now + float(error_backoff_seconds)
        except Exception as exc:
            now = time.monotonic()
            if now >= next_error_log_at:
                log_event(
                    "ap",
                    "Connectivity watchdog crashed during check",
                    level="warning",
                    data={
                        "source": "connectivity_watchdog",
                        "detail": str(exc),
                    },
                )
                next_error_log_at = now + float(error_backoff_seconds)

        time.sleep(interval_seconds)


def start_connectivity_watchdog() -> None:
    global _thread_started

    if not _env_bool("PORTAL_CONNECTIVITY_WATCHDOG_ENABLED", True):
        return

    # Flask reloader parent should not spawn background workers.
    if os.getenv("FLASK_RUN_FROM_CLI") and os.getenv("WERKZEUG_RUN_MAIN") not in ("true", "1"):
        return

    with _thread_lock:
        if _thread_started:
            return

        start_delay_seconds = _env_int("PORTAL_CONNECTIVITY_WATCHDOG_START_DELAY_SECONDS", 3, 0, 120)
        interval_seconds = _env_int("PORTAL_CONNECTIVITY_WATCHDOG_INTERVAL_SECONDS", 15, 5, 300)
        error_backoff_seconds = _env_int("PORTAL_CONNECTIVITY_WATCHDOG_ERROR_BACKOFF_SECONDS", 60, 10, 1800)

        thread = threading.Thread(
            target=_watchdog_loop,
            args=(start_delay_seconds, interval_seconds, error_backoff_seconds),
            daemon=True,
            name="connectivity-watchdog",
        )
        thread.start()
        _thread_started = True
