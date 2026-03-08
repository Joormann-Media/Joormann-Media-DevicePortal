from __future__ import annotations

import hashlib
import os
import shutil
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests
from flask import Blueprint, jsonify, request

from app.core.config import _safe_base_url, ensure_config
from app.core.device import ensure_device
from app.core.httpclient import http_get_json, http_post_json
from app.core.jsonio import write_json
from app.core.netcontrol import NetControlError, player_service_action
from app.core.paths import CONFIG_PATH
from app.core.storage_state import get_storage_state
from app.core.timeutil import utc_now

bp_stream = Blueprint('stream', __name__)


STREAM_SERVICE_NAME = os.getenv('DEVICE_PLAYER_SERVICE_NAME', 'joormann-media-deviceplayer.service')


def _selected_stream_slug(cfg: dict) -> str:
    return str(cfg.get('selected_stream_slug') or '').strip()


def _base_admin_url(cfg: dict, incoming: dict | None = None) -> str:
    incoming = incoming or {}
    return _safe_base_url(str(incoming.get('admin_base_url') or cfg.get('admin_base_url') or ''))


def _device_auth_payload(dev: dict, extra: dict | None = None) -> dict:
    payload = {
        'deviceUuid': str(dev.get('device_uuid') or '').strip(),
        'authKey': str(dev.get('auth_key') or '').strip(),
    }
    if extra:
        payload.update(extra)
    return payload


def _resolve_stream_storage_root(cfg: dict, requested_device_id: str = '') -> tuple[str, Path, str]:
    state = get_storage_state()
    known = state.get('known') if isinstance(state.get('known'), list) else []
    internal = state.get('internal') if isinstance(state.get('internal'), dict) else {}

    candidates: list[tuple[str, str, str]] = []

    for item in known:
        if not isinstance(item, dict):
            continue
        if not bool(item.get('mounted')):
            continue
        if not bool(item.get('allow_media_storage', False)):
            continue
        mount_path = str(item.get('current_mount_path') or item.get('mount_path') or '').strip()
        if not mount_path:
            continue
        candidates.append((str(item.get('id') or '').strip(), mount_path, str(item.get('drive_name') or item.get('name') or item.get('label') or 'Storage')))

    if bool(internal.get('mounted')) and bool(internal.get('allow_media_storage', False)):
        mount_path = str(internal.get('mount_path') or '').strip()
        if mount_path:
            candidates.append((str(internal.get('id') or 'internal-media').strip(), mount_path, str(internal.get('drive_name') or internal.get('name') or 'Internal LoopDrive')))

    if not candidates:
        raise RuntimeError('Kein gemounteter Speicher mit allow_media_storage=true verfügbar (LoopDrive/USB erforderlich).')

    preferred_ids = [
        str(requested_device_id or '').strip(),
        str(cfg.get('stream_storage_device_id') or '').strip(),
    ]

    chosen: tuple[str, str, str] | None = None
    for preferred in preferred_ids:
        if not preferred:
            continue
        for candidate in candidates:
            if candidate[0] == preferred:
                chosen = candidate
                break
        if chosen:
            break

    if chosen is None:
        chosen = candidates[0]

    device_id, mount_path, label = chosen
    root = Path(mount_path).resolve() / 'stream'
    root.mkdir(parents=True, exist_ok=True)
    return device_id, root, label


def _normalize_asset_url(base_url: str, raw_url: str) -> str:
    url = str(raw_url or '').strip()
    if not url:
        return ''
    if url.startswith('http://') or url.startswith('https://'):
        return url
    if not url.startswith('/'):
        url = '/' + url
    return base_url + url


def _asset_filename(asset_id: str, source_url: str) -> str:
    suffix = Path(source_url.split('?', 1)[0]).name or f'{asset_id}.bin'
    safe_suffix = ''.join(ch for ch in suffix if ch.isalnum() or ch in ('-', '_', '.')).strip('._')
    if not safe_suffix:
        safe_suffix = f'{asset_id}.bin'
    digest = hashlib.sha1(source_url.encode('utf-8')).hexdigest()[:10]
    return f'{asset_id}_{digest}_{safe_suffix}'


def _download_file(url: str, target: Path, timeout: int = 40) -> int:
    with requests.get(url, stream=True, timeout=timeout) as response:
        response.raise_for_status()
        target.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with target.open('wb') as fh:
            for chunk in response.iter_content(chunk_size=256 * 1024):
                if not chunk:
                    continue
                fh.write(chunk)
                written += len(chunk)
    return written


def _load_remote_streams(base_url: str, dev: dict) -> tuple[list[dict], str]:
    url = f"{base_url}/api/device/link/streams?" + (
        f"deviceUuid={quote(str(dev.get('device_uuid') or '').strip())}&authKey={quote(str(dev.get('auth_key') or '').strip())}"
    )
    code, payload, err = http_get_json(url, timeout=12)
    if code is None:
        raise RuntimeError(f'Adminpanel nicht erreichbar: {err}')
    if code != 200 or not isinstance(payload, dict) or not bool(payload.get('ok', False)):
        raise RuntimeError(f'Streamliste konnte nicht geladen werden (HTTP {code}).')

    data = payload.get('data') if isinstance(payload.get('data'), dict) else {}
    streams = data.get('streams') if isinstance(data.get('streams'), list) else []
    selected = str(data.get('selectedStreamSlug') or '').strip()
    return [item for item in streams if isinstance(item, dict)], selected


@bp_stream.get('/api/stream/overview')
def api_stream_overview():
    cfg = ensure_config()
    dev = ensure_device()
    base_url = _base_admin_url(cfg)

    streams: list[dict] = []
    admin_selected = ''
    fetch_error = ''
    if base_url:
        try:
            streams, admin_selected = _load_remote_streams(base_url, dev)
        except Exception as exc:
            fetch_error = str(exc)

    selected = _selected_stream_slug(cfg) or admin_selected

    storage_error = ''
    storage_info: dict = {}
    try:
        dev_id, root, label = _resolve_stream_storage_root(cfg)
        storage_info = {
            'device_id': dev_id,
            'label': label,
            'root_path': str(root),
            'current_path': str((root / 'current').resolve()),
        }
    except Exception as exc:
        storage_error = str(exc)

    status = {
        'admin_base_url': base_url,
        'selected_stream_slug': selected,
        'selected_stream_updated_at': cfg.get('selected_stream_updated_at') or '',
        'stream_manifest_version': cfg.get('stream_manifest_version') or '',
        'stream_last_sync_at': cfg.get('stream_last_sync_at') or '',
        'stream_asset_count': int(cfg.get('stream_asset_count') or 0),
        'stream_sync_error': cfg.get('stream_sync_error') or '',
    }

    player = {}
    try:
        player = player_service_action('status', STREAM_SERVICE_NAME)
    except Exception as exc:
        player = {'ok': False, 'error': str(exc)}

    return jsonify(
        ok=True,
        status=status,
        streams=streams,
        admin_selected_stream_slug=admin_selected,
        fetch_error=fetch_error,
        storage=storage_info,
        storage_error=storage_error,
        player=player,
    )


@bp_stream.post('/api/stream/select')
def api_stream_select():
    cfg = ensure_config()
    dev = ensure_device()
    data = request.get_json(force=True, silent=True) or {}
    slug = str(data.get('streamSlug') or data.get('stream_slug') or '').strip()
    if not slug:
        return jsonify(ok=False, error='stream_slug_missing', detail='streamSlug fehlt.'), 400

    cfg['selected_stream_slug'] = slug
    cfg['selected_stream_updated_at'] = utc_now()
    cfg['updated_at'] = utc_now()

    base_url = _base_admin_url(cfg, data)
    push_error = ''
    if base_url:
        payload = _device_auth_payload(dev, {'streamSlug': slug})
        code, resp, err = http_post_json(f'{base_url}/api/device/link/streams/select', payload, timeout=12)
        if code is None:
            push_error = str(err)
        elif code >= 400 or not isinstance(resp, dict) or not bool(resp.get('ok', False)):
            push_error = f'HTTP {code}'

    ok, write_err = write_json(CONFIG_PATH, cfg, mode=0o600)
    if not ok:
        return jsonify(ok=False, error='config_write_failed', detail=write_err), 500

    return jsonify(ok=True, selected_stream_slug=slug, pushed=(push_error == ''), push_error=push_error)


@bp_stream.post('/api/stream/sync')
def api_stream_sync():
    cfg = ensure_config()
    dev = ensure_device()
    data = request.get_json(force=True, silent=True) or {}

    base_url = _base_admin_url(cfg, data)
    if not base_url:
        return jsonify(ok=False, error='missing_admin_base_url', detail='Panel URL fehlt.'), 400

    stream_slug = str(data.get('streamSlug') or _selected_stream_slug(cfg)).strip()
    if not stream_slug:
        return jsonify(ok=False, error='missing_stream_slug', detail='Kein Stream ausgewählt.'), 400

    requested_device_id = str(data.get('storageDeviceId') or '').strip()
    try:
        storage_device_id, stream_root, storage_label = _resolve_stream_storage_root(cfg, requested_device_id)
    except Exception as exc:
        return jsonify(ok=False, error='storage_unavailable', detail=str(exc)), 400

    manifest_url = f"{base_url}/api/device/link/streams/{quote(stream_slug)}/player-manifest?" + (
        f"deviceUuid={quote(str(dev.get('device_uuid') or '').strip())}&authKey={quote(str(dev.get('auth_key') or '').strip())}"
    )
    code, payload, err = http_get_json(manifest_url, timeout=20)
    if code is None:
        return jsonify(ok=False, error='manifest_fetch_failed', detail=str(err)), 502
    if code != 200 or not isinstance(payload, dict) or not bool(payload.get('ok', False)):
        return jsonify(ok=False, error='manifest_fetch_failed', detail=f'HTTP {code}', panel_response=payload), (code if isinstance(code, int) and code >= 400 else 502)

    data_payload = payload.get('data') if isinstance(payload.get('data'), dict) else {}
    manifest = data_payload.get('manifest') if isinstance(data_payload.get('manifest'), dict) else None
    if not isinstance(manifest, dict):
        return jsonify(ok=False, error='manifest_invalid', detail='Ungültiges Manifest vom Adminpanel.'), 502

    assets = manifest.get('assets') if isinstance(manifest.get('assets'), dict) else {}
    playlist = manifest.get('playlist') if isinstance(manifest.get('playlist'), list) else []
    if not assets or not playlist:
        return jsonify(ok=False, error='manifest_empty', detail='Manifest enthält keine Assets oder Playlist.'), 400

    timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
    staging_dir = stream_root / 'staging' / f'build-{timestamp}'
    staging_assets = staging_dir / 'assets'
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging_assets.mkdir(parents=True, exist_ok=True)

    current_dir = stream_root / 'current'
    current_manifest_path = current_dir / 'manifest.json'
    current_manifest = {}
    if current_manifest_path.exists():
        try:
            import json
            current_manifest = json.loads(current_manifest_path.read_text(encoding='utf-8'))
        except Exception:
            current_manifest = {}

    current_assets = current_manifest.get('assets') if isinstance(current_manifest, dict) and isinstance(current_manifest.get('assets'), dict) else {}

    rewritten_assets: dict[str, str] = {}
    source_assets: dict[str, str] = {}
    downloaded: list[dict] = []

    try:
        for asset_id, source_url in assets.items():
            asset_key = str(asset_id).strip()
            source_value = str(source_url or '').strip()
            if not asset_key or not source_value:
                continue

            remote_url = _normalize_asset_url(base_url, source_value)
            filename = _asset_filename(asset_key, remote_url)
            relative_local = f'assets/{filename}'
            target_path = staging_dir / relative_local

            reused = False
            if isinstance(current_assets, dict):
                for _, curr_path in current_assets.items():
                    if str(curr_path) == relative_local:
                        candidate = current_dir / relative_local
                        if candidate.exists() and candidate.is_file():
                            target_path.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(candidate, target_path)
                            reused = True
                        break

            if not reused:
                size = _download_file(remote_url, target_path)
            else:
                size = target_path.stat().st_size if target_path.exists() else 0

            rewritten_assets[asset_key] = relative_local
            source_assets[asset_key] = remote_url
            downloaded.append({'asset': asset_key, 'path': relative_local, 'bytes': int(size), 'reused': reused})

        manifest_local = dict(manifest)
        manifest_local['assets'] = rewritten_assets

        import json
        (staging_dir / 'manifest.json').write_text(
            json.dumps(manifest_local, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        (staging_dir / 'manifest-source.json').write_text(
            json.dumps({'source_assets': source_assets, 'stream_slug': stream_slug, 'synced_at': utc_now()}, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )

        current_prev = stream_root / f'current.prev.{timestamp}'
        if current_prev.exists():
            shutil.rmtree(current_prev, ignore_errors=True)

        if current_dir.exists():
            current_dir.rename(current_prev)
        staging_dir.rename(current_dir)
        shutil.rmtree(current_prev, ignore_errors=True)
    except Exception as exc:
        shutil.rmtree(staging_dir, ignore_errors=True)
        cfg['stream_sync_error'] = str(exc)
        cfg['updated_at'] = utc_now()
        write_json(CONFIG_PATH, cfg, mode=0o600)
        return jsonify(ok=False, error='stream_sync_failed', detail=str(exc)), 500

    cfg['selected_stream_slug'] = stream_slug
    cfg['selected_stream_updated_at'] = utc_now()
    cfg['stream_storage_device_id'] = storage_device_id
    cfg['stream_storage_label'] = storage_label
    cfg['stream_storage_path'] = str(stream_root)
    cfg['stream_current_path'] = str(current_dir)
    cfg['stream_manifest_version'] = str(manifest.get('version') or '')
    cfg['stream_last_sync_at'] = utc_now()
    cfg['stream_asset_count'] = len(rewritten_assets)
    cfg['stream_sync_error'] = ''
    cfg['updated_at'] = utc_now()
    ok, write_err = write_json(CONFIG_PATH, cfg, mode=0o600)
    if not ok:
        return jsonify(ok=False, error='config_write_failed', detail=write_err), 500

    return jsonify(
        ok=True,
        stream_slug=stream_slug,
        manifest_version=cfg['stream_manifest_version'],
        asset_count=len(rewritten_assets),
        storage={
            'device_id': storage_device_id,
            'label': storage_label,
            'stream_root': str(stream_root),
            'current_path': str(current_dir),
        },
        downloaded=downloaded,
    )


@bp_stream.get('/api/stream/player/status')
def api_stream_player_status():
    try:
        payload = player_service_action('status', STREAM_SERVICE_NAME)
        return jsonify(ok=True, player=payload)
    except NetControlError as exc:
        status = 500 if exc.code in ('execution_failed', 'script_missing') else 400
        return jsonify(ok=False, error=exc.code, detail=exc.detail or exc.message), status


@bp_stream.post('/api/stream/player/<action>')
def api_stream_player_action(action: str):
    action = str(action or '').strip().lower()
    if action not in {'start', 'stop', 'restart'}:
        return jsonify(ok=False, error='invalid_action', detail='Action muss start|stop|restart sein.'), 400
    try:
        payload = player_service_action(action, STREAM_SERVICE_NAME)
        return jsonify(ok=True, player=payload)
    except NetControlError as exc:
        status = 500 if exc.code in ('execution_failed', 'script_missing') else 400
        return jsonify(ok=False, error=exc.code, detail=exc.detail or exc.message), status
