from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

from app.api import routes_panel
from app.api.routes_stream import refresh_llm_manager_from_runtime
from app.core.config import ensure_config
from app.core.jsonio import write_json
from app.core.paths import CONFIG_PATH
from app.core.display import get_display_snapshot
from app.core.display_screenshots import (
    get_screenshot_info,
    clear_all_screenshots,
    delete_screenshot,
    capture_and_upload,
    maybe_auto_capture,
)
from app.core.device import ensure_device
from app.core.family_registry_push import maybe_push_family_registry
from app.core.fingerprint import collect_fingerprint, ensure_fingerprint, short_fingerprint
from app.core.gitinfo import get_repo_update_info, get_update_info
from app.core.netcontrol import NetControlError, get_network_info
from app.core.storage_state import get_storage_state
from app.core.state import get_state, update_state
from app.core.systeminfo import format_uptime_human, parse_cpu_temp_c, parse_load_stats, parse_mem_stats_kb, parse_uptime_seconds

bp_status = Blueprint('status', __name__)
_RUNTIME_SNAPSHOT_CACHE: dict[str, object] = {}
_REPO_UPDATES_CACHE: dict[str, object] = {"data": None, "updated_at": None}
_REPO_UPDATES_TTL = timedelta(minutes=2)


def _mask_secret(value: str, keep: int = 6) -> str:
    value = value or ''
    if len(value) <= keep:
        return '*' * len(value)
    return '********' + value[-keep:]


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_repo_link(value: str) -> str:
    out = str(value or "").strip().lower()
    if out.endswith("/"):
        out = out.rstrip("/")
    if out.endswith(".git"):
        out = out[:-4]
    return out


def _repo_update_key(repo_link: str, install_dir: str, service_name: str) -> str:
    link = _normalize_repo_link(repo_link)
    install = str(install_dir or "").strip().lower()
    service = str(service_name or "").strip().lower()
    if install:
        return f"install:{install}"
    if link:
        return f"repo:{link}"
    return f"service:{service}"


def _collect_repo_updates(cfg: dict, force: bool = False) -> dict:
    now = datetime.now(timezone.utc)
    cached_data = _REPO_UPDATES_CACHE.get("data")
    cached_at = _REPO_UPDATES_CACHE.get("updated_at")
    if (
        not force
        and isinstance(cached_data, dict)
        and isinstance(cached_at, datetime)
        and (now - cached_at) < _REPO_UPDATES_TTL
    ):
        return cached_data

    items: list[dict] = []
    repo_root = Path(__file__).resolve().parents[2]

    portal_update = get_update_info()
    portal_local = str(portal_update.get("local_version") or "").strip() or str(portal_update.get("local_commit") or "").strip()[:12]
    items.append({
        "id": "fixed:device-portal",
        "key": _repo_update_key("device-portal", str(repo_root), "device-portal.service"),
        "name": "Device-Portal",
        "kind": "fixed",
        "repo_link": str(repo_root),
        "install_dir": str(repo_root),
        "service_name": "device-portal.service",
        "available": bool(portal_update.get("available")),
        "error": str(portal_update.get("error") or "").strip(),
        "local_version": portal_local,
        "local_commit": str(portal_update.get("local_commit") or "").strip(),
        "remote_commit": str(portal_update.get("remote_commit") or "").strip(),
        "local_branch": str(portal_update.get("local_branch") or "").strip(),
        "checked": True,
    })

    player_repo_path = routes_panel._resolve_player_repo_path(cfg)
    player_repo_link = str(cfg.get("player_repo_link") or cfg.get("player_repo_dir") or "").strip()
    player_service_name = str(cfg.get("player_service_name") or "").strip() or "joormann-media-jarvis-displayplayer.service"
    player_update = get_repo_update_info(player_repo_path)
    player_local = str(player_update.get("local_version") or "").strip() or str(player_update.get("local_commit") or "").strip()[:12]
    items.append({
        "id": "fixed:display-player",
        "key": _repo_update_key(player_repo_link, player_repo_path, player_service_name),
        "name": "Joormann-Media-Jarvis-DisplayPlayer",
        "kind": "fixed",
        "repo_link": player_repo_link,
        "install_dir": str(player_repo_path or "").strip(),
        "service_name": player_service_name,
        "available": bool(player_update.get("available")),
        "error": str(player_update.get("error") or "").strip(),
        "local_version": player_local,
        "local_commit": str(player_update.get("local_commit") or "").strip(),
        "remote_commit": str(player_update.get("remote_commit") or "").strip(),
        "local_branch": str(player_update.get("local_branch") or "").strip(),
        "checked": True,
    })

    managed_raw = cfg.get("managed_install_repos")
    managed_repos = managed_raw if isinstance(managed_raw, list) else []
    seen_keys: set[str] = set()
    for raw in managed_repos:
        if not isinstance(raw, dict):
            continue
        repo_link = str(raw.get("repo_link") or raw.get("repo_dir") or "").strip()
        install_dir = str(raw.get("install_dir") or "").strip()
        service_name = str(raw.get("service_name") or "").strip()
        name = str(raw.get("name") or "").strip() or "Repo"
        if not repo_link and not install_dir:
            continue
        key = _repo_update_key(repo_link, install_dir, service_name)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        update = get_repo_update_info(install_dir)
        local = str(update.get("local_version") or "").strip() or str(update.get("local_commit") or "").strip()[:12]
        items.append({
            "id": str(raw.get("id") or key),
            "key": key,
            "name": name,
            "kind": "managed",
            "repo_link": repo_link,
            "install_dir": install_dir,
            "service_name": service_name,
            "available": bool(update.get("available")),
            "error": str(update.get("error") or "").strip(),
            "local_version": local,
            "local_commit": str(update.get("local_commit") or "").strip(),
            "remote_commit": str(update.get("remote_commit") or "").strip(),
            "local_branch": str(update.get("local_branch") or "").strip(),
            "checked": True,
        })

    update_count = sum(1 for item in items if bool(item.get("available")))
    summary = {
        "checked_at": _iso_now(),
        "has_updates": update_count > 0,
        "update_count": update_count,
        "items": items,
    }
    _REPO_UPDATES_CACHE["data"] = summary
    _REPO_UPDATES_CACHE["updated_at"] = now
    return summary


def _collect_status_payload(force_repo_updates: bool = False) -> dict:
    cfg = ensure_config()
    dev = ensure_device()
    fp = ensure_fingerprint()
    mode = 'play' if (cfg.get('selected_stream_slug') or '').strip() else 'setup'
    state, _ = update_state(cfg, dev, fp, mode=mode, message='status')

    dev_view = dict(dev)
    dev_view['auth_key'] = _mask_secret(dev_view.get('auth_key', ''))
    player_repo_path = routes_panel._resolve_player_repo_path(cfg)
    player_update = get_repo_update_info(player_repo_path)
    repo_updates = _collect_repo_updates(cfg, force=force_repo_updates)

    display_snapshot = get_display_snapshot(cfg)
    if isinstance(display_snapshot, dict):
        displays = display_snapshot.get("displays") if isinstance(display_snapshot.get("displays"), list) else []
        primary = display_snapshot.get("primary_display") if isinstance(display_snapshot.get("primary_display"), dict) else {}
        primary_connector = str(primary.get("connector") or "").strip()
        primary_connected = bool(primary.get("connected"))
        if primary_connector and primary_connected:
            try:
                if maybe_auto_capture(cfg, primary_connector):
                    write_json(CONFIG_PATH, cfg, mode=0o600)
            except Exception:
                pass
        screenshots: dict[str, dict] = {}
        for item in displays:
            connector = str(item.get("connector") or "").strip()
            if not connector:
                continue
            if not bool(item.get("connected")):
                screenshots[connector] = {"available": False}
                continue
            info = get_screenshot_info(connector)
            screenshots[connector] = info.to_dict() if info else {"available": False}
        display_snapshot = dict(display_snapshot)
        display_snapshot["screenshots"] = screenshots

    return {
        "config": cfg,
        "device": dev_view,
        "fingerprint": short_fingerprint(fp),
        "display": display_snapshot,
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
        "repo_updates": repo_updates,
        "state": state,
    }


def _build_runtime_viewmodel() -> dict:
    legacy: dict[str, object] = {}
    legacy["status"] = _collect_status_payload(force_repo_updates=True)
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
        cached_summary = cfg.get("system_update_summary")
        legacy["system_update_summary"] = cached_summary if isinstance(cached_summary, dict) else {}
    except Exception:
        legacy["system_update_summary"] = {}
    try:
        cfg = ensure_config()
        refresh_llm_manager_from_runtime(cfg, force=True)
    except Exception:
        pass
    try:
        cfg = ensure_config()
        llm_manager = cfg.get("llm_manager")
        legacy["llm_manager"] = llm_manager if isinstance(llm_manager, dict) else {}
    except Exception:
        legacy["llm_manager"] = {}
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


@bp_status.post('/api/status/check-updates')
def api_status_check_updates():
    payload = _collect_status_payload(force_repo_updates=True)
    return jsonify(ok=True, **payload)


@bp_status.post('/api/runtime/warmup')
def api_runtime_warmup():
    cfg = ensure_config()
    viewmodel = _build_runtime_viewmodel()
    _RUNTIME_SNAPSHOT_CACHE["viewmodel"] = viewmodel
    _RUNTIME_SNAPSHOT_CACHE["updated_at"] = _iso_now()
    push_result = maybe_push_family_registry(cfg, "warmup")

    # Screenshot capture on warmup — disabled, set to True to re-enable
    _SCREENSHOTS_ON_WARMUP_ENABLED = False
    screenshot_results: dict[str, object] = {}
    if _SCREENSHOTS_ON_WARMUP_ENABLED:
        try:
            display_snapshot = get_display_snapshot(cfg)
            displays = display_snapshot.get("displays") if isinstance(display_snapshot, dict) else []
            if isinstance(displays, list):
                for item in displays:
                    if not bool(item.get("connected")):
                        continue
                    connector = str(item.get("connector") or "").strip()
                    if not connector:
                        continue
                    try:
                        payload = capture_and_upload(cfg, connector, allow_upload=True)
                        screenshot_results[connector] = payload.get("screenshot") or {"available": False}
                    except Exception as exc:
                        screenshot_results[connector] = {"available": False, "error": str(exc)}
            write_json(CONFIG_PATH, cfg, mode=0o600)
        except Exception as exc:
            screenshot_results["_error"] = str(exc)

    return jsonify(
        ok=True,
        data={
            "status": "ready",
            "progress": 100,
            "updated_at": _RUNTIME_SNAPSHOT_CACHE["updated_at"],
            "registry_push": push_result,
            "screenshots": screenshot_results,
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
    display_snapshot = get_display_snapshot(cfg)
    if isinstance(display_snapshot, dict):
        displays = display_snapshot.get("displays") if isinstance(display_snapshot.get("displays"), list) else []
        screenshots: dict[str, dict] = {}
        for item in displays:
            connector = str(item.get("connector") or "").strip()
            if not connector:
                continue
            if not bool(item.get("connected")):
                screenshots[connector] = {"available": False}
                continue
            info = get_screenshot_info(connector)
            screenshots[connector] = info.to_dict() if info else {"available": False}
        display_snapshot = dict(display_snapshot)
        display_snapshot["screenshots"] = screenshots
    return jsonify(ok=True, display=display_snapshot)


@bp_status.get('/api/display/screenshot/<connector>')
def api_display_screenshot(connector: str):
    info = get_screenshot_info(connector, cache_bust=False)
    if not info:
        return jsonify(ok=False, error="not_found"), 404
    return send_file(info.file_path, mimetype="image/png")


@bp_status.post('/api/display/screenshot/<connector>/capture')
def api_display_screenshot_capture(connector: str):
    cfg = ensure_config()
    snapshot = get_display_snapshot(cfg)
    displays = snapshot.get("displays") if isinstance(snapshot, dict) else []
    is_connected = False
    if isinstance(displays, list):
        for item in displays:
            if str(item.get("connector") or "").strip() == str(connector or "").strip():
                is_connected = bool(item.get("connected"))
                break
    if not is_connected:
        return jsonify(ok=False, error="display_not_connected", message="Display ist nicht verbunden."), 400
    try:
        payload = capture_and_upload(cfg, connector, allow_upload=True)
    except NetControlError as exc:
        return jsonify(ok=False, error=exc.code, detail=exc.detail or exc.message), 400
    return jsonify(ok=True, data=payload)


@bp_status.post('/api/display/screenshot/<connector>/delete')
def api_display_screenshot_delete(connector: str):
    removed = delete_screenshot(connector)
    return jsonify(ok=True, removed=bool(removed))


@bp_status.post('/api/display/screenshots/clear')
def api_display_screenshot_clear():
    removed = clear_all_screenshots()
    return jsonify(ok=True, removed=int(removed))


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


_UPDATE_HISTORY_MAX = 50

_ALLOWED_HISTORY_KINDS = frozenset({"portal", "player", "managed_repo"})
_ALLOWED_HISTORY_STATUSES = frozenset({"done", "failed", "running", "unknown"})


@bp_status.get('/api/update-history')
def api_get_update_history():
    cfg = ensure_config()
    items = cfg.get('update_history')
    if not isinstance(items, list):
        items = []
    return jsonify(ok=True, items=items)


@bp_status.post('/api/update-history/record')
def api_record_update_history():
    cfg = ensure_config()
    body = request.get_json(force=True, silent=True) or {}

    kind = str(body.get('kind') or 'unknown').strip()
    if kind not in _ALLOWED_HISTORY_KINDS:
        kind = 'unknown'

    status = str(body.get('status') or 'unknown').strip().lower()
    if status not in _ALLOWED_HISTORY_STATUSES:
        status = 'unknown'

    entry: dict = {
        'ts': _iso_now(),
        'kind': kind,
        'name': str(body.get('name') or '').strip()[:120],
        'status': status,
        'success': bool(body.get('success')),
        'git_status': str(body.get('git_status') or '').strip()[:64],
        'before_commit': str(body.get('before_commit') or '').strip()[:12],
        'after_commit': str(body.get('after_commit') or '').strip()[:12],
        'service_name': str(body.get('service_name') or '').strip()[:120],
        'job_id': str(body.get('job_id') or '').strip()[:96],
        'duration': str(body.get('duration') or '').strip()[:32],
    }

    history = cfg.get('update_history')
    if not isinstance(history, list):
        history = []

    history.insert(0, entry)
    if len(history) > _UPDATE_HISTORY_MAX:
        history = history[:_UPDATE_HISTORY_MAX]

    cfg['update_history'] = history
    write_json(CONFIG_PATH, cfg, mode=0o600)

    return jsonify(ok=True, entry=entry)


@bp_status.delete('/api/update-history')
def api_clear_update_history():
    cfg = ensure_config()
    cfg['update_history'] = []
    write_json(CONFIG_PATH, cfg, mode=0o600)
    return jsonify(ok=True)
