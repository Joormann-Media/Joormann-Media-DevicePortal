from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, jsonify

from app.api import routes_panel
from app.core.config import ensure_config
from app.core.display import get_display_snapshot
from app.core.device import ensure_device
from app.core.fingerprint import collect_fingerprint, ensure_fingerprint, short_fingerprint
from app.core.gitinfo import get_repo_update_info, get_update_info
from app.core.netcontrol import NetControlError, get_network_info
from app.core.netcontrol import spotify_connect_service_action
from app.core.storage_state import get_storage_state
from app.core.state import get_state, update_state
from app.core.systeminfo import format_uptime_human, parse_cpu_temp_c, parse_load_stats, parse_mem_stats_kb, parse_uptime_seconds

bp_status = Blueprint('status', __name__)
_RUNTIME_SNAPSHOT_CACHE: dict[str, object] = {}


def _mask_secret(value: str, keep: int = 6) -> str:
    value = value or ''
    if len(value) <= keep:
        return '*' * len(value)
    return '********' + value[-keep:]


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _collect_status_payload() -> dict:
    cfg = ensure_config()
    dev = ensure_device()
    fp = ensure_fingerprint()
    mode = 'play' if (cfg.get('selected_stream_slug') or '').strip() else 'setup'
    state, _ = update_state(cfg, dev, fp, mode=mode, message='status')

    dev_view = dict(dev)
    dev_view['auth_key'] = _mask_secret(dev_view.get('auth_key', ''))
    player_repo_path = routes_panel._resolve_player_repo_path(cfg)
    player_update = get_repo_update_info(player_repo_path)

    return {
        "config": cfg,
        "device": dev_view,
        "fingerprint": short_fingerprint(fp),
        "display": get_display_snapshot(cfg),
        "system": {
            "memory": parse_mem_stats_kb(),
            "load": parse_load_stats(),
            "cpu": {
                "temperature_c": parse_cpu_temp_c(),
            },
            "uptime_seconds": parse_uptime_seconds(),
            "uptime_human": format_uptime_human(parse_uptime_seconds()),
        },
        "app_update": get_update_info(),
        "player_update": player_update,
        "state": state,
    }


def _build_runtime_viewmodel() -> dict:
    legacy: dict[str, object] = {}
    legacy["status"] = _collect_status_payload()
    try:
        legacy["network"] = get_network_info()
    except NetControlError as exc:
        legacy["network"] = {"ok": False, "error": exc.code, "detail": exc.detail or exc.message}
    try:
        legacy["storage"] = get_storage_state()
    except NetControlError as exc:
        legacy["storage"] = {"ok": False, "error": exc.code, "detail": exc.detail or exc.message}
    try:
        cfg = ensure_config()
        legacy["spotify_connect"] = spotify_connect_service_action(
            "status",
            str(cfg.get("spotify_connect_service_name") or "").strip(),
            service_user=str(cfg.get("spotify_connect_service_user") or "").strip(),
            service_scope=str(cfg.get("spotify_connect_service_scope") or "").strip(),
            service_candidates=str(cfg.get("spotify_connect_service_candidates") or "").strip(),
        )
    except NetControlError as exc:
        legacy["spotify_connect"] = {"ok": False, "error": exc.code, "detail": exc.detail or exc.message}

    # DevicePortal classic UI expects these keys, even when empty.
    legacy.setdefault("software_requirements", {})
    legacy.setdefault("sentinels_status", {})

    return {
        "generated_at": _iso_now(),
        "sections": {
            "legacy": legacy,
        },
    }


@bp_status.get('/health')
def health():
    return jsonify(ok=True)


@bp_status.get('/api/status')
def api_status():
    payload = _collect_status_payload()
    return jsonify(ok=True, **payload)


@bp_status.post('/api/runtime/warmup')
def api_runtime_warmup():
    viewmodel = _build_runtime_viewmodel()
    _RUNTIME_SNAPSHOT_CACHE["viewmodel"] = viewmodel
    _RUNTIME_SNAPSHOT_CACHE["updated_at"] = _iso_now()
    return jsonify(
        ok=True,
        data={
            "status": "ready",
            "progress": 100,
            "updated_at": _RUNTIME_SNAPSHOT_CACHE["updated_at"],
        },
    )


@bp_status.get('/api/runtime/viewmodel')
def api_runtime_viewmodel():
    cached = _RUNTIME_SNAPSHOT_CACHE.get("viewmodel")
    if not isinstance(cached, dict):
        cached = _build_runtime_viewmodel()
        _RUNTIME_SNAPSHOT_CACHE["viewmodel"] = cached
        _RUNTIME_SNAPSHOT_CACHE["updated_at"] = _iso_now()
    return jsonify(ok=True, data=cached)


@bp_status.post('/api/runtime/refresh/<section>')
def api_runtime_refresh(section: str):
    _ = (section or "").strip().lower()
    viewmodel = _build_runtime_viewmodel()
    _RUNTIME_SNAPSHOT_CACHE["viewmodel"] = viewmodel
    _RUNTIME_SNAPSHOT_CACHE["updated_at"] = _iso_now()
    return jsonify(
        ok=True,
        data={
            "status": "refreshed",
            "section": section,
            "updated_at": _RUNTIME_SNAPSHOT_CACHE["updated_at"],
        },
    )


@bp_status.get('/api/display/info')
def api_display_info():
    cfg = ensure_config()
    return jsonify(ok=True, display=get_display_snapshot(cfg))


@bp_status.get('/api/fingerprint')
def api_fingerprint():
    fp = ensure_fingerprint()
    return jsonify(ok=True, fingerprint=fp)


@bp_status.post('/api/fingerprint/refresh')
def api_fingerprint_refresh():
    fp = collect_fingerprint()
    cfg = ensure_config()
    dev = ensure_device()
    update_state(cfg, dev, fp, mode='play' if cfg.get('selected_stream_slug') else 'setup', message='fingerprint refreshed')
    return jsonify(ok=True, fingerprint=fp)


@bp_status.post('/api/status/fingerprint/refresh')
def api_status_fingerprint_refresh():
    return api_fingerprint_refresh()


@bp_status.get('/api/state')
def api_state():
    return jsonify(ok=True, state=get_state())


@bp_status.get('/api/status/state')
def api_status_state():
    return api_state()
