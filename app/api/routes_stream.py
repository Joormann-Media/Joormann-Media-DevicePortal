from __future__ import annotations

import json
import hashlib
import os
import shutil
import fcntl
import socket
from datetime import datetime
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
from flask import Blueprint, jsonify, request

from app.core.config import _safe_base_url, ensure_config
from app.core.display import get_display_snapshot
from app.core.device import ensure_device
from app.core.httpclient import http_get_json, http_post_json
from app.core.jsonio import write_json
from app.core.netcontrol import NetControlError, player_service_action, player_service_install, player_update, player_update_status
from app.core.paths import PORTAL_DIR
from app.core.netcontrol import spotify_connect_service_action
from app.core.paths import CONFIG_PATH, DATA_DIR
from app.core.storage_state import get_storage_state
from app.core.timeutil import utc_now

bp_stream = Blueprint('stream', __name__)


STREAM_SERVICE_NAME = os.getenv('DEVICE_PLAYER_SERVICE_NAME', 'joormann-media-deviceplayer.service')
PLAYER_SOURCE_PATH = Path(DATA_DIR) / 'player-source.json'
PLAYER_CONTROL_DEFAULT_HOST = os.getenv('DEVICEPLAYER_CONTROL_API_HOST', '127.0.0.1').strip() or '127.0.0.1'
try:
    PLAYER_CONTROL_DEFAULT_PORT = int(os.getenv('DEVICEPLAYER_CONTROL_API_PORT', '5081'))
except Exception:
    PLAYER_CONTROL_DEFAULT_PORT = 5081


def _selected_stream_slug(cfg: dict) -> str:
    return str(cfg.get('selected_stream_slug') or '').strip()


def _selected_stream_name(cfg: dict) -> str:
    return str(cfg.get('selected_stream_name') or '').strip()


def _player_control_base_url() -> str:
    host = str(PLAYER_CONTROL_DEFAULT_HOST or '127.0.0.1').strip() or '127.0.0.1'
    if host in ('0.0.0.0', '::'):
        host = '127.0.0.1'
    port = int(PLAYER_CONTROL_DEFAULT_PORT or 5081)
    return f'http://{host}:{port}'


def _player_control_request(method: str, path: str, payload: dict | None = None, timeout: int = 8) -> tuple[int | None, dict, str]:
    url = f"{_player_control_base_url()}{path}"
    try:
        if method.upper() == 'GET':
            resp = requests.get(url, timeout=timeout)
        else:
            resp = requests.post(url, json=payload or {}, timeout=timeout)
    except Exception as exc:
        return None, {}, str(exc)

    status = int(resp.status_code)
    try:
        data = resp.json() if resp.text else {}
    except Exception:
        data = {'raw': resp.text}
    if not isinstance(data, dict):
        data = {'raw': data}
    return status, data, ''


def _resolve_audio_allowed_root() -> Path:
    explicit = str(os.getenv('DEVICEPLAYER_AUDIO_ALLOWED_ROOT') or '').strip()
    if explicit:
        return Path(explicit).expanduser().resolve()

    if PLAYER_SOURCE_PATH.exists():
        try:
            payload = json.loads(PLAYER_SOURCE_PATH.read_text(encoding='utf-8'))
            if isinstance(payload, dict):
                manifest = payload.get('manifest') if isinstance(payload.get('manifest'), dict) else {}
                manifest_path = str(manifest.get('path') or payload.get('manifest_path') or '').strip()
                if manifest_path:
                    return (Path(manifest_path).expanduser().resolve().parent / 'audio').resolve()
        except Exception:
            pass

    return Path('/mnt/deviceportal/media/stream/current/audio').resolve()


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


def _sanitize_stream_folder_name(stream_name: str, stream_slug: str) -> str:
    raw = (stream_name or '').strip()
    if not raw:
        raw = (stream_slug or '').strip()
    if not raw:
        raw = 'stream'

    safe_chars = []
    last_was_sep = False
    for ch in raw:
        if ch.isalnum():
            safe_chars.append(ch.lower())
            last_was_sep = False
            continue
        if ch in (' ', '-', '_', '.'):
            if not last_was_sep:
                safe_chars.append('-')
                last_was_sep = True
            continue
        # drop unsupported chars

    folder = ''.join(safe_chars).strip('-')
    if folder == '':
        folder = 'stream'
    if len(folder) > 120:
        folder = folder[:120].rstrip('-') or 'stream'
    return folder


def _extract_stream_name(streams: list[dict], stream_slug: str) -> str:
    slug = str(stream_slug or '').strip()
    if slug == '':
        return ''
    for item in streams:
        if not isinstance(item, dict):
            continue
        item_slug = str(item.get('slug') or item.get('streamSlug') or '').strip()
        if item_slug != slug:
            continue
        name = str(item.get('name') or item.get('streamName') or item.get('title') or '').strip()
        if name:
            return name
    return ''


def _normalize_asset_url(base_url: str, raw_url: str) -> str:
    url = str(raw_url or '').strip()
    if not url:
        return ''
    if url.startswith('http://') or url.startswith('https://'):
        return url
    if not url.startswith('/'):
        url = '/' + url
    return base_url + url


def _asset_url_candidates(base_url: str, raw_url: str) -> list[str]:
    original = _normalize_asset_url(base_url, raw_url)
    parsed = urlparse(original)
    host_prefix = f'{parsed.scheme}://{parsed.netloc}'
    path = parsed.path or ''

    candidates: list[str] = []

    def _add(url: str) -> None:
        if url and url not in candidates:
            candidates.append(url)

    _add(original)
    if path.startswith('/uploads/') and not path.startswith('/uploads/uploads/'):
        _add(host_prefix + '/uploads' + path)
    if path.startswith('/uploads/uploads/'):
        _add(host_prefix + path.replace('/uploads/uploads/', '/uploads/', 1))

    return candidates


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


def _download_asset_with_fallback(base_url: str, raw_url: str, target: Path, timeout: int = 40) -> tuple[str, int]:
    last_exc: Exception | None = None
    attempts: list[str] = []
    for candidate in _asset_url_candidates(base_url, raw_url):
        attempts.append(candidate)
        try:
            size = _download_file(candidate, target, timeout=timeout)
            return candidate, size
        except Exception as exc:
            last_exc = exc
    detail = f'asset download failed after {len(attempts)} attempts'
    if attempts:
        detail += f': {attempts}'
    if last_exc is not None:
        detail += f' ({last_exc})'
    raise RuntimeError(detail)


def _load_manifest_file(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        raise RuntimeError(f'Manifest ist kein JSON-Objekt: {path}')
    return payload


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _release_lock(lock_file) -> None:
    try:
        if lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
    except Exception:
        pass


def _write_player_source_file(
    *,
    stream_slug: str,
    storage_device_id: str,
    storage_label: str,
    stream_root: Path,
    current_dir: Path,
    manifest_version: str,
    manifest_sha256: str,
    asset_count: int,
    display_rotation_degrees: int = 0,
    display_mount_orientation: str = "unknown",
    display_content_orientation: str = "landscape",
) -> None:
    payload = {
        'version': 1,
        'updated_at': utc_now(),
        'stream_slug': stream_slug,
        'storage': {
            'device_id': storage_device_id,
            'label': storage_label,
            'stream_root': str(stream_root),
            'current_path': str(current_dir),
        },
        'manifest': {
            'path': str(current_dir / 'manifest.json'),
            'version': manifest_version,
            'sha256': manifest_sha256,
            'asset_count': int(asset_count),
        },
        'display': {
            'rotation_degrees': int(display_rotation_degrees),
            'mount_orientation': str(display_mount_orientation or 'unknown'),
            'content_orientation': str(display_content_orientation or 'landscape'),
        },
    }
    PLAYER_SOURCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLAYER_SOURCE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


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
    selected_name = _selected_stream_name(cfg)

    storage_error = ''
    storage_info: dict = {}
    try:
        dev_id, root, label = _resolve_stream_storage_root(cfg)
        stream_folder = _sanitize_stream_folder_name(selected_name, selected) if selected else ''
        stream_root = (root / stream_folder).resolve() if stream_folder else root
        current_path = (stream_root / 'current').resolve() if stream_folder else (root / 'current').resolve()
        storage_info = {
            'device_id': dev_id,
            'label': label,
            'root_path': str(root),
            'stream_root': str(stream_root),
            'current_path': str(current_path),
        }
    except Exception as exc:
        storage_error = str(exc)

    status = {
        'admin_base_url': base_url,
        'selected_stream_slug': selected,
        'selected_stream_name': selected_name,
        'selected_stream_updated_at': cfg.get('selected_stream_updated_at') or '',
        'stream_manifest_version': cfg.get('stream_manifest_version') or '',
        'stream_manifest_sha256': cfg.get('stream_manifest_sha256') or '',
        'stream_last_sync_at': cfg.get('stream_last_sync_at') or '',
        'stream_asset_count': int(cfg.get('stream_asset_count') or 0),
        'stream_sync_error': cfg.get('stream_sync_error') or '',
    }

    player = {}
    try:
        player = player_service_action('status', STREAM_SERVICE_NAME)
    except Exception as exc:
        player = {'ok': False, 'error': str(exc)}

    spotify_connect = {}
    try:
        spotify_connect = spotify_connect_service_action(
            'status',
            str(cfg.get('spotify_connect_service_name') or '').strip(),
            service_user=str(cfg.get('spotify_connect_service_user') or '').strip(),
            service_scope=str(cfg.get('spotify_connect_service_scope') or '').strip(),
            service_candidates=str(cfg.get('spotify_connect_service_candidates') or '').strip(),
        )
    except Exception as exc:
        spotify_connect = {'ok': False, 'error': str(exc)}

    return jsonify(
        ok=True,
        status=status,
        streams=streams,
        admin_selected_stream_slug=admin_selected,
        fetch_error=fetch_error,
        storage=storage_info,
        storage_error=storage_error,
        player=player,
        spotify_connect=spotify_connect,
    )


@bp_stream.post('/api/stream/select')
def api_stream_select():
    cfg = ensure_config()
    dev = ensure_device()
    data = request.get_json(force=True, silent=True) or {}
    slug = str(data.get('streamSlug') or data.get('stream_slug') or '').strip()
    stream_name = str(data.get('streamName') or data.get('stream_name') or '').strip()
    if not slug:
        return jsonify(ok=False, error='stream_slug_missing', detail='streamSlug fehlt.'), 400

    cfg['selected_stream_slug'] = slug
    if stream_name:
        cfg['selected_stream_name'] = stream_name
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

    return jsonify(
        ok=True,
        selected_stream_slug=slug,
        selected_stream_name=str(cfg.get('selected_stream_name') or ''),
        pushed=(push_error == ''),
        push_error=push_error,
    )


@bp_stream.post('/api/stream/sync')
def api_stream_sync():
    cfg = ensure_config()
    dev = ensure_device()
    data = request.get_json(force=True, silent=True) or {}

    base_url = _base_admin_url(cfg, data)
    if not base_url:
        return jsonify(ok=False, error='missing_admin_base_url', detail='Panel URL fehlt.'), 400

    stream_slug = str(data.get('streamSlug') or _selected_stream_slug(cfg)).strip()
    stream_name = str(data.get('streamName') or data.get('stream_name') or _selected_stream_name(cfg)).strip()
    if not stream_slug:
        return jsonify(ok=False, error='missing_stream_slug', detail='Kein Stream ausgewählt.'), 400

    requested_device_id = str(data.get('storageDeviceId') or '').strip()
    try:
        storage_device_id, stream_base_root, storage_label = _resolve_stream_storage_root(cfg, requested_device_id)
    except Exception as exc:
        return jsonify(ok=False, error='storage_unavailable', detail=str(exc)), 400

    if not stream_name:
        try:
            streams, _ = _load_remote_streams(base_url, dev)
            stream_name = _extract_stream_name(streams, stream_slug)
        except Exception:
            stream_name = ''

    stream_folder = _sanitize_stream_folder_name(stream_name, stream_slug)
    stream_root = stream_base_root / stream_folder
    stream_root.mkdir(parents=True, exist_ok=True)

    lock_path = stream_root / '.sync.lock'
    lock_file = None
    try:
        lock_file = lock_path.open('w')
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        if lock_file:
            lock_file.close()
        return jsonify(ok=False, error='stream_sync_locked', detail='Ein anderer Stream-Sync läuft bereits.'), 409
    except Exception as exc:
        if lock_file:
            lock_file.close()
        return jsonify(ok=False, error='stream_sync_lock_failed', detail=str(exc)), 500

    manifest_url = f"{base_url}/api/device/link/streams/{quote(stream_slug)}/player-manifest?" + (
        f"deviceUuid={quote(str(dev.get('device_uuid') or '').strip())}&authKey={quote(str(dev.get('auth_key') or '').strip())}"
    )
    code, payload, err = http_get_json(manifest_url, timeout=20)
    if code is None:
        _release_lock(lock_file)
        return jsonify(ok=False, error='manifest_fetch_failed', detail=str(err)), 502
    if code != 200 or not isinstance(payload, dict) or not bool(payload.get('ok', False)):
        _release_lock(lock_file)
        return jsonify(ok=False, error='manifest_fetch_failed', detail=f'HTTP {code}', panel_response=payload), (code if isinstance(code, int) and code >= 400 else 502)

    data_payload = payload.get('data') if isinstance(payload.get('data'), dict) else {}
    manifest = data_payload.get('manifest') if isinstance(data_payload.get('manifest'), dict) else None
    if not isinstance(manifest, dict):
        _release_lock(lock_file)
        return jsonify(ok=False, error='manifest_invalid', detail='Ungültiges Manifest vom Adminpanel.'), 502

    assets = manifest.get('assets') if isinstance(manifest.get('assets'), dict) else {}
    playlist = manifest.get('playlist') if isinstance(manifest.get('playlist'), list) else []
    if not assets or not playlist:
        _release_lock(lock_file)
        return jsonify(ok=False, error='manifest_empty', detail='Manifest enthält keine Assets oder Playlist.'), 400

    try:
        timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S%f')
        staging_dir = stream_root / 'staging' / f'build-{timestamp}'
        staging_assets = staging_dir / 'assets'
        staging_dir.mkdir(parents=True, exist_ok=True)
        staging_assets.mkdir(parents=True, exist_ok=True)

        current_dir = stream_root / 'current'
        current_manifest_path = current_dir / 'manifest.json'
        current_manifest = {}
        if current_manifest_path.exists():
            try:
                current_manifest = _load_manifest_file(current_manifest_path)
            except Exception:
                current_manifest = {}

        current_assets = current_manifest.get('assets') if isinstance(current_manifest.get('assets'), dict) else {}

        rewritten_assets: dict[str, str] = {}
        source_assets: dict[str, str] = {}
        downloaded: list[dict] = []
        manifest_sha256 = ''
        current_prev = stream_root / f'current.prev.{timestamp}'
        current_was_renamed = False
        published = False

        try:
            for asset_id, source_url in assets.items():
                asset_key = str(asset_id).strip()
                source_value = str(source_url or '').strip()
                if not asset_key or not source_value:
                    continue

                filename = _asset_filename(asset_key, source_value)
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
                    remote_url, size = _download_asset_with_fallback(base_url, source_value, target_path)
                else:
                    size = target_path.stat().st_size if target_path.exists() else 0
                    remote_url = _normalize_asset_url(base_url, source_value)

                rewritten_assets[asset_key] = relative_local
                source_assets[asset_key] = remote_url
                downloaded.append({'asset': asset_key, 'path': relative_local, 'bytes': int(size), 'reused': reused})

            manifest_local = dict(manifest)
            manifest_local['assets'] = rewritten_assets

            (staging_dir / 'manifest.json').write_text(
                json.dumps(manifest_local, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
            (staging_dir / 'manifest-source.json').write_text(
                json.dumps({'source_assets': source_assets, 'stream_slug': stream_slug, 'synced_at': utc_now()}, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )

            _load_manifest_file(staging_dir / 'manifest.json')

            if current_prev.exists():
                shutil.rmtree(current_prev, ignore_errors=True)

            if current_dir.exists():
                current_dir.rename(current_prev)
                current_was_renamed = True
            staging_dir.rename(current_dir)
            published = True

            _load_manifest_file(current_dir / 'manifest.json')
            manifest_sha256 = _file_sha256(current_dir / 'manifest.json')

            if current_prev.exists():
                shutil.rmtree(current_prev, ignore_errors=True)
        except Exception as exc:
            rollback_error = ''
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
            if (not published) and current_was_renamed and current_prev.exists() and (not current_dir.exists()):
                try:
                    current_prev.rename(current_dir)
                except Exception as restore_exc:
                    rollback_error = f' | rollback_failed: {restore_exc}'
            detail = f'{exc}{rollback_error}'
            cfg['stream_sync_error'] = detail
            cfg['updated_at'] = utc_now()
            write_json(CONFIG_PATH, cfg, mode=0o600)
            return jsonify(ok=False, error='stream_sync_failed', detail=detail), 500

        cfg['selected_stream_slug'] = stream_slug
        cfg['selected_stream_name'] = stream_name
        cfg['selected_stream_updated_at'] = utc_now()
        cfg['stream_storage_device_id'] = storage_device_id
        cfg['stream_storage_label'] = storage_label
        cfg['stream_storage_base_path'] = str(stream_base_root)
        cfg['stream_storage_folder'] = stream_folder
        cfg['stream_storage_path'] = str(stream_root)
        cfg['stream_current_path'] = str(current_dir)
        cfg['stream_manifest_version'] = str(manifest.get('version') or '')
        cfg['stream_manifest_sha256'] = manifest_sha256
        cfg['stream_last_sync_at'] = utc_now()
        cfg['stream_asset_count'] = len(rewritten_assets)
        cfg['stream_sync_error'] = ''
        cfg['updated_at'] = utc_now()

        display_snapshot = get_display_snapshot(cfg)
        primary_display = display_snapshot.get('primary_display') if isinstance(display_snapshot, dict) else {}
        if not isinstance(primary_display, dict):
            primary_display = {}

        _write_player_source_file(
            stream_slug=stream_slug,
            storage_device_id=storage_device_id,
            storage_label=storage_label,
            stream_root=stream_root,
            current_dir=current_dir,
            manifest_version=cfg['stream_manifest_version'],
            manifest_sha256=cfg['stream_manifest_sha256'],
            asset_count=len(rewritten_assets),
            display_rotation_degrees=int(primary_display.get('rotation_degrees') or 0),
            display_mount_orientation=str(primary_display.get('mount_orientation') or 'unknown'),
            display_content_orientation=str(primary_display.get('content_orientation') or 'landscape'),
        )

        ok, write_err = write_json(CONFIG_PATH, cfg, mode=0o600)
        if not ok:
            return jsonify(ok=False, error='config_write_failed', detail=write_err), 500

        return jsonify(
            ok=True,
            stream_slug=stream_slug,
            manifest_version=cfg['stream_manifest_version'],
            manifest_sha256=cfg['stream_manifest_sha256'],
            asset_count=len(rewritten_assets),
            storage={
                'device_id': storage_device_id,
                'label': storage_label,
                'stream_folder': stream_folder,
                'stream_root': str(stream_root),
                'current_path': str(current_dir),
            },
            downloaded=downloaded,
        )
    finally:
        _release_lock(lock_file)


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


@bp_stream.post('/api/stream/player/service/install')
def api_stream_player_service_install():
    cfg = ensure_config()
    data = request.get_json(force=True, silent=True) or {}
    repo_link = str(data.get('player_repo_link') or data.get('player_repo_dir') or cfg.get('player_repo_link') or cfg.get('player_repo_dir') or '').strip()
    service_name = str(data.get('player_service_name') or cfg.get('player_service_name') or STREAM_SERVICE_NAME).strip() or STREAM_SERVICE_NAME
    service_user = str(data.get('player_service_user') or cfg.get('player_service_user') or '').strip()

    if not repo_link:
        return jsonify(ok=False, error='player_repo_missing', detail='Bitte Player-Repo-Link oder lokalen Pfad setzen.'), 400
    if not service_user:
        return jsonify(ok=False, error='player_service_user_missing', detail='Bitte Service-User setzen.'), 400

    try:
        payload = player_service_install(repo_link, service_user=service_user, service_name=service_name, portal_dir=str(PORTAL_DIR))
    except NetControlError as exc:
        status = 500 if exc.code in ('script_missing', 'execution_failed', 'player_service_install_failed') else 400
        return jsonify(ok=False, error=exc.code, detail=exc.detail or exc.message), status

    return jsonify(ok=True, player=payload)


@bp_stream.get('/api/stream/player/repo')
def api_stream_player_repo_get():
    cfg = ensure_config()
    repo_link = str(cfg.get('player_repo_link') or cfg.get('player_repo_dir') or '')
    service_name = str(cfg.get('player_service_name') or STREAM_SERVICE_NAME)
    service_user = str(cfg.get('player_service_user') or '')
    install_dir = str(cfg.get('player_install_dir') or '').strip()

    if not install_dir:
        raw_repos = cfg.get('managed_install_repos')
        repos = raw_repos if isinstance(raw_repos, list) else []
        normalized_repo_link = repo_link.strip().lower()
        normalized_service = service_name.strip().lower()
        matched = None
        for item in repos:
            if not isinstance(item, dict):
                continue
            item_link = str(item.get('repo_link') or item.get('repo_dir') or '').strip().lower()
            item_service = str(item.get('service_name') or '').strip().lower()
            if normalized_repo_link and item_link and item_link == normalized_repo_link:
                matched = item
                break
            if normalized_service and item_service and item_service == normalized_service:
                matched = item
                break
        if isinstance(matched, dict):
            install_dir = str(matched.get('install_dir') or '').strip()
            if not service_user:
                service_user = str(matched.get('service_user') or '').strip()
            if install_dir:
                cfg['player_install_dir'] = install_dir
                if service_user:
                    cfg['player_service_user'] = service_user
                cfg['updated_at'] = utc_now()
                write_json(CONFIG_PATH, cfg, mode=0o600)

    return jsonify(
        ok=True,
        config={
            'player_repo_link': repo_link,
            'player_service_name': service_name,
            'player_service_user': service_user,
            'player_install_dir': install_dir,
        },
    )


@bp_stream.post('/api/stream/player/repo')
def api_stream_player_repo_set():
    cfg = ensure_config()
    data = request.get_json(force=True, silent=True) or {}
    repo_link = str(data.get('player_repo_link') or data.get('player_repo_dir') or '').strip()
    service_name = str(data.get('player_service_name') or STREAM_SERVICE_NAME).strip() or STREAM_SERVICE_NAME
    service_user = str(data.get('player_service_user') or '').strip()
    install_dir = str(data.get('player_install_dir') or '').strip()

    cfg['player_repo_link'] = repo_link
    cfg['player_repo_dir'] = repo_link
    cfg['player_service_name'] = service_name
    cfg['player_service_user'] = service_user
    cfg['player_install_dir'] = install_dir
    cfg['updated_at'] = utc_now()
    ok, write_err = write_json(CONFIG_PATH, cfg, mode=0o600)
    if not ok:
        return jsonify(ok=False, error='config_write_failed', detail=write_err), 500

    return jsonify(ok=True, config={
        'player_repo_link': repo_link,
        'player_service_name': service_name,
        'player_service_user': service_user,
        'player_install_dir': install_dir,
    })


def _repo_default_name(repo_link: str) -> str:
    value = str(repo_link or '').strip().rstrip('/')
    if not value:
        return 'Repo'
    tail = value.split('/')[-1].strip() or value
    if tail.endswith('.git'):
        tail = tail[:-4].strip() or tail
    return tail


def _repo_default_service_name(repo_link: str) -> str:
    base = _repo_default_name(repo_link).strip().lower()
    safe = []
    last_dash = False
    for ch in base:
        if ch.isalnum():
            safe.append(ch)
            last_dash = False
            continue
        if ch in (' ', '-', '_', '.'):
            if not last_dash:
                safe.append('-')
                last_dash = True
    slug = ''.join(safe).strip('-')
    if not slug:
        slug = 'service'
    return f'{slug}.service'


def _sanitize_managed_repo_entry(item: dict) -> dict:
    source = item if isinstance(item, dict) else {}
    repo_id = str(source.get('id') or '').strip()
    name = str(source.get('name') or '').strip()
    repo_link = str(source.get('repo_link') or source.get('repo_dir') or '').strip()
    service_name = str(source.get('service_name') or '').strip()
    service_user = str(source.get('service_user') or '').strip()
    install_dir = str(source.get('install_dir') or '').strip()
    if service_name in {'-', '—', 'none', 'null'}:
        service_name = ''
    if service_user in {'-', '—', 'none', 'null'}:
        service_user = ''
    if install_dir in {'-', '—', 'none', 'null'}:
        install_dir = ''
    use_service_raw = source.get('use_service')
    autostart_raw = source.get('autostart')
    api_base_url = str(source.get('api_base_url') or '').strip()
    health_url = str(source.get('health_url') or '').strip()
    ui_url = str(source.get('ui_url') or source.get('web_url') or '').strip()
    hostname = str(source.get('hostname') or '').strip()
    node_name = str(source.get('node_name') or '').strip()
    endpoints_raw = source.get('endpoints') if isinstance(source.get('endpoints'), dict) else {}
    source_name = str(source.get('source') or '').strip()
    first_seen_at = str(source.get('first_seen_at') or '').strip()
    last_seen_at = str(source.get('last_seen_at') or '').strip()
    tags_raw = source.get('tags') if isinstance(source.get('tags'), list) else []
    caps_raw = source.get('capabilities') if isinstance(source.get('capabilities'), list) else []
    cached_status = source.get('service_status') if isinstance(source.get('service_status'), dict) else {}
    cached_checked_at = str(source.get('service_status_checked_at') or '').strip()
    try:
        service_port = int(source.get('service_port')) if source.get('service_port') not in (None, '') else None
    except Exception:
        service_port = None
    created_at = str(source.get('created_at') or '').strip()
    use_service = True if use_service_raw is None else bool(use_service_raw)
    autostart = True if autostart_raw is None else bool(autostart_raw)
    if use_service and not service_name and repo_link:
        service_name = _repo_default_service_name(repo_link)

    if not repo_id:
        seed = f"{name}|{repo_link}|{service_name}|{service_user}"
        repo_id = hashlib.sha1(seed.encode('utf-8')).hexdigest()[:12]
    if not name:
        name = _repo_default_name(repo_link)
    if not created_at:
        created_at = utc_now()

    return {
        'id': repo_id,
        'name': name,
        'repo_link': repo_link,
        'service_name': service_name,
        'service_user': service_user,
        'install_dir': install_dir,
        'use_service': use_service,
        'autostart': autostart,
        'api_base_url': api_base_url,
        'health_url': health_url,
        'ui_url': ui_url,
        'hostname': hostname,
        'node_name': node_name,
        'endpoints': endpoints_raw,
        'service_port': service_port,
        'source': source_name,
        'first_seen_at': first_seen_at,
        'last_seen_at': last_seen_at,
        'tags': [str(item).strip() for item in tags_raw if str(item).strip()],
        'capabilities': [str(item).strip() for item in caps_raw if str(item).strip()],
        'service_status': cached_status,
        'service_status_checked_at': cached_checked_at,
        'created_at': created_at,
        'updated_at': utc_now(),
    }


def _managed_repos_from_config(cfg: dict) -> list[dict]:
    raw = cfg.get('managed_install_repos')
    if not isinstance(raw, list):
        return []

    repos: list[dict] = []
    for item in raw:
        sanitized = _sanitize_managed_repo_entry(item if isinstance(item, dict) else {})
        if sanitized.get('repo_link'):
            repos.append(sanitized)
    return repos


def _repo_identity_key(repo_link: str, install_dir: str = '') -> str:
    link = str(repo_link or '').strip().lower()
    install = str(install_dir or '').strip().lower()
    return f'{link}|{install}'


def _repo_matches(repo_link_a: str, install_dir_a: str, repo_link_b: str, install_dir_b: str) -> bool:
    link_a = str(repo_link_a or '').strip().lower()
    link_b = str(repo_link_b or '').strip().lower()
    if not link_a or not link_b or link_a != link_b:
        return False
    dir_a = str(install_dir_a or '').strip().lower()
    dir_b = str(install_dir_b or '').strip().lower()
    return not dir_a or not dir_b or dir_a == dir_b


def _is_private_remote(remote: str) -> bool:
    value = str(remote or '').strip()
    if not value:
        return False
    try:
        addr = ip_address(value)
    except ValueError:
        return False
    return bool(addr.is_loopback or addr.is_private)


def _sanitize_autodiscover_entry(data: dict, remote_addr: str) -> dict:
    payload = data if isinstance(data, dict) else {}
    repo_link = str(payload.get('repo_link') or payload.get('repo_url') or '').strip()
    repo_name = str(payload.get('repo_name') or payload.get('name') or _repo_default_name(repo_link)).strip()
    repo_branch = str(payload.get('repo_branch') or payload.get('branch') or 'main').strip() or 'main'
    service_name = str(payload.get('service_name') or '').strip()
    service_user = str(payload.get('service_user') or '').strip()
    install_dir = str(payload.get('install_dir') or '').strip()
    use_service = bool(payload.get('use_service', True))
    autostart = bool(payload.get('autostart', True))
    api_base_url = str(payload.get('api_base_url') or '').strip()
    health_url = str(payload.get('health_url') or '').strip()
    ui_url = str(payload.get('ui_url') or payload.get('web_url') or '').strip()
    hostname = str(payload.get('hostname') or '').strip()
    instance_id = str(payload.get('instance_id') or '').strip()
    node_name = str(payload.get('node_name') or '').strip()
    endpoints_raw = payload.get('endpoints') if isinstance(payload.get('endpoints'), dict) else {}
    tags = payload.get('tags') if isinstance(payload.get('tags'), list) else []
    capabilities = payload.get('capabilities') if isinstance(payload.get('capabilities'), list) else []
    try:
        service_port = int(payload.get('service_port')) if payload.get('service_port') not in (None, '') else None
    except Exception:
        service_port = None

    seed = instance_id or f'{remote_addr}|{repo_link}|{service_name}|{install_dir}|{repo_name}'
    entry_id = hashlib.sha1(seed.encode('utf-8')).hexdigest()[:16]
    return {
        'id': entry_id,
        'instance_id': instance_id,
        'repo_name': repo_name,
        'repo_link': repo_link,
        'repo_branch': repo_branch,
        'service_name': service_name,
        'service_user': service_user,
        'install_dir': install_dir,
        'use_service': use_service,
        'autostart': autostart,
        'api_base_url': api_base_url,
        'health_url': health_url,
        'ui_url': ui_url,
        'hostname': hostname,
        'node_name': node_name,
        'endpoints': endpoints_raw,
        'remote_addr': remote_addr,
        'service_port': service_port,
        'tags': [str(item).strip() for item in tags if str(item).strip()],
        'capabilities': [str(item).strip() for item in capabilities if str(item).strip()],
        'source': 'autodiscover',
        'updated_at': utc_now(),
    }


def _autodiscover_seed_service_status(item: dict) -> dict:
    payload = item if isinstance(item, dict) else {}
    use_service = bool(payload.get('use_service', True))
    api_base_url = str(payload.get('api_base_url') or '').strip().rstrip('/')
    health_url = str(payload.get('health_url') or '').strip()
    runtime_url = health_url or (f'{api_base_url}/health' if api_base_url else '')
    runtime_reachable_hint = bool(runtime_url)
    if not use_service:
        return {
            'checked': True,
            'use_service': False,
            'service_name': str(payload.get('service_name') or '').strip(),
            'service_installed': False,
            'service_enabled': False,
            'service_enabled_state': 'disabled',
            'service_running': runtime_reachable_hint,
            'active_state': 'inactive',
            'substate': 'not-applicable',
            'runtime_reachable': runtime_reachable_hint,
            'runtime_url': runtime_url,
            'source': 'autodiscover-seed',
        }
    service_name = str(payload.get('service_name') or '').strip()
    return {
        'checked': True,
        'use_service': True,
        'service_name': service_name,
        'service_installed': True,
        'service_enabled': bool(payload.get('autostart', True)),
        'service_enabled_state': 'enabled' if bool(payload.get('autostart', True)) else 'disabled',
        'service_running': runtime_reachable_hint,
        'active_state': 'active' if runtime_reachable_hint else 'unknown',
        'substate': 'running' if runtime_reachable_hint else 'unknown',
        'runtime_reachable': runtime_reachable_hint,
        'runtime_url': runtime_url,
        'source': 'autodiscover-seed',
    }


def _current_host_aliases() -> set[str]:
    aliases: set[str] = set()
    for value in (socket.gethostname(), os.getenv('HOSTNAME', '')):
        raw = str(value or '').strip().lower()
        if not raw:
            continue
        aliases.add(raw)
        aliases.add(raw.split('.')[0])
    aliases.update({'localhost', '127.0.0.1', '::1'})
    return aliases


def _host_from_url(raw_url: str) -> str:
    value = str(raw_url or '').strip()
    if not value:
        return ''
    try:
        return str(urlparse(value).hostname or '').strip().lower()
    except Exception:
        return ''


def _is_remote_autodiscover_repo(repo: dict) -> bool:
    source = str((repo or {}).get('source') or '').strip().lower()
    if source != 'autodiscover':
        return False
    local_aliases = _current_host_aliases()
    candidates = {
        str((repo or {}).get('hostname') or '').strip().lower(),
        str((repo or {}).get('node_name') or '').strip().lower(),
        _host_from_url(str((repo or {}).get('api_base_url') or '')),
        _host_from_url(str((repo or {}).get('health_url') or '')),
    }
    candidates = {item for item in candidates if item}
    if not candidates:
        return False
    for item in candidates:
        short = item.split('.')[0]
        if item in local_aliases or short in local_aliases:
            return False
    return True


def _path_browser_allowed_roots(cfg: dict) -> list[Path]:
    candidates: list[Path] = []
    service_user = str(cfg.get('player_service_user') or '').strip()
    if service_user:
        candidates.append(Path(f'/home/{service_user}'))
    env_user = str(os.getenv('USER') or '').strip()
    if env_user:
        candidates.append(Path(f'/home/{env_user}'))
    candidates.extend([Path('/mnt'), Path('/media'), Path('/opt'), Path('/srv')])

    roots: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except Exception:
            continue
        key = str(resolved)
        if key in seen:
            continue
        if not resolved.exists() or not resolved.is_dir():
            continue
        seen.add(key)
        roots.append(resolved)
    return roots


def _path_browser_resolve(raw_path: str, roots: list[Path]) -> tuple[Path, Path]:
    if not roots:
        raise RuntimeError('Kein gültiger Root-Pfad für Dateibrowser verfügbar.')

    selected = str(raw_path or '').strip()
    current = roots[0] if not selected else Path(selected).expanduser()
    current = current.resolve()

    matched_root = None
    for root in roots:
        try:
            if current.is_relative_to(root):
                matched_root = root
                break
        except Exception:
            continue
    if matched_root is None:
        raise RuntimeError('Pfad liegt außerhalb der erlaubten Verzeichnisse.')
    if not current.exists() or not current.is_dir():
        raise RuntimeError('Verzeichnis existiert nicht.')
    return current, matched_root


def _audio_path_browser_root() -> Path:
    configured = str(os.getenv('DEVICEPLAYER_AUDIO_BROWSER_ROOT') or '').strip()
    candidate = Path(configured).expanduser() if configured else Path('/mnt')
    resolved = candidate.resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise RuntimeError('Audio-Dateibrowser Root nicht verfügbar.')
    return resolved


def _audio_path_browser_resolve(raw_path: str, root: Path) -> Path:
    selected = str(raw_path or '').strip()
    current = root if not selected else Path(selected).expanduser()
    current = current.resolve()
    if not current.exists() or not current.is_dir():
        raise RuntimeError('Verzeichnis existiert nicht.')
    try:
        if not current.is_relative_to(root):
            raise RuntimeError('Pfad liegt außerhalb von /mnt.')
    except Exception:
        raise RuntimeError('Pfad liegt außerhalb von /mnt.')
    return current


def _managed_repo_service_status(repo: dict) -> dict:
    def _probe_runtime_health(target: dict) -> dict:
        urls: list[str] = []
        health_url = str(target.get('health_url') or '').strip()
        api_base_url = str(target.get('api_base_url') or '').strip().rstrip('/')
        if health_url:
            urls.append(health_url)
        if api_base_url:
            urls.append(f'{api_base_url}/health')
        checked: list[str] = []
        for url in urls:
            if not url:
                continue
            checked.append(url)
            try:
                resp = requests.get(url, timeout=1.5)
                if 200 <= int(resp.status_code) < 500:
                    return {'reachable': True, 'url': url, 'checked_urls': checked}
            except Exception:
                continue
        return {'reachable': False, 'url': checked[0] if checked else '', 'checked_urls': checked}

    runtime = _probe_runtime_health(repo)
    remote_autodiscover = _is_remote_autodiscover_repo(repo)
    use_service = bool(repo.get('use_service', True))
    repo_link = str(repo.get('repo_link') or '').strip()
    service_name = str(repo.get('service_name') or '').strip() or _repo_default_service_name(repo_link)
    cached = repo.get('service_status') if isinstance(repo.get('service_status'), dict) else {}
    if remote_autodiscover:
        runtime_reachable = bool(runtime.get('reachable'))
        running = runtime_reachable
        installed = bool(cached.get('service_installed')) or runtime_reachable or bool(repo.get('api_base_url') or repo.get('health_url'))
        enabled = bool(repo.get('autostart', True))
        return {
            'checked': True,
            'use_service': use_service,
            'remote': True,
            'service_name': service_name,
            'service_installed': installed,
            'service_enabled': enabled,
            'service_enabled_state': 'enabled' if enabled else 'disabled',
            'service_running': running,
            'active_state': 'active' if running else 'inactive',
            'substate': 'running' if running else 'dead',
            'runtime_reachable': runtime_reachable,
            'runtime_url': str(runtime.get('url') or ''),
            'source': 'autodiscover-remote',
        }
    if not use_service:
        return {
            'checked': True,
            'use_service': False,
            'service_name': service_name,
            'service_installed': False,
            'service_enabled': False,
            'service_enabled_state': 'disabled',
            'service_running': bool(runtime.get('reachable')),
            'active_state': 'inactive',
            'substate': 'not-applicable',
            'runtime_reachable': bool(runtime.get('reachable')),
            'runtime_url': str(runtime.get('url') or ''),
        }
    try:
        payload = player_service_action('status', service_name)
        service_installed = bool(payload.get('service_installed'))
        service_running = bool(payload.get('active'))
        return {
            'checked': True,
            'use_service': True,
            'service_name': service_name,
            'service_installed': service_installed,
            'service_enabled': bool(payload.get('service_enabled')),
            'service_enabled_state': str(payload.get('service_enabled_state') or ''),
            'service_running': service_running,
            'active_state': str(payload.get('active_state') or ''),
            'substate': str(payload.get('substate') or ''),
            'runtime_reachable': bool(runtime.get('reachable')),
            'runtime_url': str(runtime.get('url') or ''),
        }
    except NetControlError as exc:
        return {
            'checked': False,
            'use_service': True,
            'service_name': service_name,
            'service_installed': False,
            'service_enabled': False,
            'service_enabled_state': '',
            'service_running': bool(runtime.get('reachable')),
            'active_state': '',
            'substate': '',
            'error': exc.code,
            'message': exc.detail or exc.message,
            'runtime_reachable': bool(runtime.get('reachable')),
            'runtime_url': str(runtime.get('url') or ''),
        }


def _is_truthy(value: str) -> bool:
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on'}


@bp_stream.get('/api/stream/player/repos')
def api_stream_player_repos_get():
    cfg = ensure_config()
    live = _is_truthy(str(request.args.get('live') or ''))
    repos = _managed_repos_from_config(cfg)
    changed = False
    for item in repos:
        status = item.get('service_status') if isinstance(item.get('service_status'), dict) else {}
        needs_status_refresh = live or not status
        if needs_status_refresh:
            item['service_status'] = _managed_repo_service_status(item)
            item['service_status_checked_at'] = utc_now()
            changed = True
    if changed:
        cfg['managed_install_repos'] = repos
        cfg['updated_at'] = utc_now()
        write_json(CONFIG_PATH, cfg, mode=0o600)
    repos = sorted(repos, key=lambda item: str(item.get('name') or '').lower())
    return jsonify(ok=True, data={'repos': repos})


@bp_stream.post('/autodiscover')
@bp_stream.post('/api/autodiscover/register')
def api_autodiscover_register():
    remote = str(request.remote_addr or '').strip()
    if not _is_private_remote(remote):
        return jsonify(ok=False, error='forbidden_remote', detail='Autodiscover ist nur aus privatem Netzwerk erlaubt.'), 403

    cfg = ensure_config()
    data = request.get_json(force=True, silent=True) or {}
    item = _sanitize_autodiscover_entry(data, remote)
    if not item.get('repo_link'):
        return jsonify(ok=False, error='repo_link_missing', detail='repo_link/repo_url fehlt.'), 400

    entries = cfg.get('autodiscover_services')
    if not isinstance(entries, list):
        entries = []
    existing_idx = next((idx for idx, current in enumerate(entries) if str((current or {}).get('id') or '').strip() == item['id']), -1)
    if existing_idx >= 0:
        current = entries[existing_idx] if isinstance(entries[existing_idx], dict) else {}
        item['first_seen_at'] = str(current.get('first_seen_at') or item['updated_at'])
        entries[existing_idx] = {**current, **item, 'last_seen_at': item['updated_at']}
    else:
        item['first_seen_at'] = item['updated_at']
        item['last_seen_at'] = item['updated_at']
        entries.append(item)

    entries = sorted(entries, key=lambda row: str((row or {}).get('last_seen_at') or ''), reverse=True)[:300]
    cfg['autodiscover_services'] = entries

    repos = _managed_repos_from_config(cfg)
    managed_item = _sanitize_managed_repo_entry(
        {
            'name': str(item.get('repo_name') or '').strip(),
            'repo_link': str(item.get('repo_link') or '').strip(),
            'service_name': str(item.get('service_name') or '').strip(),
            'service_user': str(item.get('service_user') or '').strip(),
            'install_dir': str(item.get('install_dir') or '').strip(),
            'use_service': bool(item.get('use_service', True)),
            'autostart': bool(item.get('autostart', True)),
            'api_base_url': str(item.get('api_base_url') or '').strip(),
            'health_url': str(item.get('health_url') or '').strip(),
            'ui_url': str(item.get('ui_url') or '').strip(),
            'hostname': str(item.get('hostname') or '').strip(),
            'node_name': str(item.get('node_name') or '').strip(),
            'endpoints': item.get('endpoints') if isinstance(item.get('endpoints'), dict) else {},
            'service_port': item.get('service_port'),
            'source': 'autodiscover',
            'first_seen_at': str(item.get('first_seen_at') or item.get('updated_at') or ''),
            'last_seen_at': str(item.get('last_seen_at') or item.get('updated_at') or ''),
            'tags': item.get('tags') if isinstance(item.get('tags'), list) else [],
            'capabilities': item.get('capabilities') if isinstance(item.get('capabilities'), list) else [],
            'service_status': _autodiscover_seed_service_status(item),
            'service_status_checked_at': utc_now(),
        }
    )
    existing_repo_idx = next(
        (
            idx for idx, current in enumerate(repos)
            if str(current.get('repo_link') or '').strip().lower() == str(managed_item.get('repo_link') or '').strip().lower()
            and str(current.get('install_dir') or '').strip().lower() == str(managed_item.get('install_dir') or '').strip().lower()
        ),
        -1,
    )
    if existing_repo_idx >= 0:
        current = repos[existing_repo_idx]
        managed_item['id'] = str(current.get('id') or managed_item['id'])
        managed_item['created_at'] = str(current.get('created_at') or managed_item['created_at'])
        repos[existing_repo_idx] = managed_item
    else:
        repos.append(managed_item)
    cfg['managed_install_repos'] = repos

    cfg['updated_at'] = utc_now()
    ok, write_err = write_json(CONFIG_PATH, cfg, mode=0o600)
    if not ok:
        return jsonify(ok=False, error='config_write_failed', detail=write_err), 500

    return jsonify(ok=True, data={'item': item, 'count': len(entries)})


@bp_stream.get('/api/autodiscover/services')
def api_autodiscover_services():
    cfg = ensure_config()
    raw = cfg.get('autodiscover_services')
    entries = raw if isinstance(raw, list) else []
    managed_repos = _managed_repos_from_config(cfg)
    normalized = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        discovered_link = str(item.get('repo_link') or '').strip()
        discovered_dir = str(item.get('install_dir') or '').strip()
        if any(
            _repo_matches(discovered_link, discovered_dir, current.get('repo_link') or '', current.get('install_dir') or '')
            for current in managed_repos
        ):
            continue
        normalized.append(item)
    normalized = sorted(normalized, key=lambda row: str(row.get('last_seen_at') or row.get('updated_at') or ''), reverse=True)
    return jsonify(ok=True, data={'services': normalized})


@bp_stream.post('/api/autodiscover/services/<service_id>/promote')
def api_autodiscover_promote(service_id: str):
    cfg = ensure_config()
    target_id = str(service_id or '').strip()
    if not target_id:
        return jsonify(ok=False, error='service_id_missing', detail='Service-ID fehlt.'), 400

    raw = cfg.get('autodiscover_services')
    discovered = raw if isinstance(raw, list) else []
    target = next((item for item in discovered if isinstance(item, dict) and str(item.get('id') or '').strip() == target_id), None)
    if not target:
        return jsonify(ok=False, error='service_not_found', detail='Autodiscover-Service nicht gefunden.'), 404

    repos = _managed_repos_from_config(cfg)
    promoted = _sanitize_managed_repo_entry(
        {
            'name': str(target.get('repo_name') or '').strip(),
            'repo_link': str(target.get('repo_link') or '').strip(),
            'service_name': str(target.get('service_name') or '').strip(),
            'service_user': str(target.get('service_user') or '').strip(),
            'install_dir': str(target.get('install_dir') or '').strip(),
            'use_service': bool(target.get('use_service', True)),
            'autostart': bool(target.get('autostart', True)),
            'api_base_url': str(target.get('api_base_url') or '').strip(),
            'health_url': str(target.get('health_url') or '').strip(),
            'ui_url': str(target.get('ui_url') or '').strip(),
            'hostname': str(target.get('hostname') or '').strip(),
            'node_name': str(target.get('node_name') or '').strip(),
            'endpoints': target.get('endpoints') if isinstance(target.get('endpoints'), dict) else {},
            'service_port': target.get('service_port'),
            'source': 'autodiscover',
            'first_seen_at': str(target.get('first_seen_at') or target.get('updated_at') or ''),
            'last_seen_at': str(target.get('last_seen_at') or target.get('updated_at') or ''),
            'tags': target.get('tags') if isinstance(target.get('tags'), list) else [],
            'capabilities': target.get('capabilities') if isinstance(target.get('capabilities'), list) else [],
            'service_status': _autodiscover_seed_service_status(target),
            'service_status_checked_at': utc_now(),
        }
    )

    idx = next((i for i, row in enumerate(repos) if str(row.get('repo_link') or '').strip().lower() == str(promoted.get('repo_link') or '').strip().lower()), -1)
    if idx >= 0:
        promoted['id'] = str(repos[idx].get('id') or promoted['id'])
        promoted['created_at'] = str(repos[idx].get('created_at') or promoted['created_at'])
        repos[idx] = promoted
    else:
        repos.append(promoted)

    cfg['managed_install_repos'] = repos
    cfg['autodiscover_services'] = [
        item for item in discovered
        if isinstance(item, dict) and str(item.get('id') or '').strip() != target_id
    ]
    cfg['updated_at'] = utc_now()
    ok, write_err = write_json(CONFIG_PATH, cfg, mode=0o600)
    if not ok:
        return jsonify(ok=False, error='config_write_failed', detail=write_err), 500
    return jsonify(ok=True, data={'item': promoted, 'repos': sorted(repos, key=lambda row: str(row.get('name') or '').lower())})


@bp_stream.get('/api/stream/player/path-browser')
def api_stream_player_path_browser():
    cfg = ensure_config()
    roots = _path_browser_allowed_roots(cfg)
    if not roots:
        return jsonify(ok=False, error='path_browser_unavailable', detail='Keine erlaubten Root-Verzeichnisse gefunden.'), 500

    try:
        current, root = _path_browser_resolve(str(request.args.get('path') or ''), roots)
    except RuntimeError as exc:
        return jsonify(ok=False, error='invalid_path', detail=str(exc)), 400

    directories: list[dict] = []
    try:
        for child in sorted(current.iterdir(), key=lambda item: item.name.lower()):
            try:
                if not child.is_dir():
                    continue
            except Exception:
                continue
            directories.append(
                {
                    'name': child.name,
                    'path': str(child.resolve()),
                }
            )
    except Exception as exc:
        return jsonify(ok=False, error='path_browser_failed', detail=str(exc)), 500

    parent_path = ''
    if current != root:
        parent_candidate = current.parent.resolve()
        try:
            if parent_candidate.is_relative_to(root):
                parent_path = str(parent_candidate)
            else:
                parent_path = str(root)
        except Exception:
            parent_path = str(root)

    return jsonify(
        ok=True,
        data={
            'current_path': str(current),
            'root_path': str(root),
            'parent_path': parent_path,
            'roots': [str(item) for item in roots],
            'directories': directories,
        },
    )


@bp_stream.post('/api/stream/player/repos')
def api_stream_player_repos_set():
    cfg = ensure_config()
    data = request.get_json(force=True, silent=True) or {}
    repo_link = str(data.get('repo_link') or data.get('repo_dir') or '').strip()
    if not repo_link:
        return jsonify(ok=False, error='repo_link_missing', detail='Repo Link/Pfad fehlt.'), 400

    target_id = str(data.get('id') or '').strip()
    repos = _managed_repos_from_config(cfg)
    item = _sanitize_managed_repo_entry(
        {
            'id': target_id,
            'name': str(data.get('name') or '').strip(),
            'repo_link': repo_link,
            'service_name': str(data.get('service_name') or '').strip(),
            'service_user': str(data.get('service_user') or '').strip(),
            'install_dir': str(data.get('install_dir') or '').strip(),
            'use_service': data.get('use_service', True),
            'autostart': data.get('autostart', True),
            'api_base_url': str(data.get('api_base_url') or '').strip(),
            'health_url': str(data.get('health_url') or '').strip(),
            'ui_url': str(data.get('ui_url') or '').strip(),
            'hostname': str(data.get('hostname') or '').strip(),
            'node_name': str(data.get('node_name') or '').strip(),
            'endpoints': data.get('endpoints') if isinstance(data.get('endpoints'), dict) else {},
            'service_port': data.get('service_port'),
            'source': str(data.get('source') or '').strip(),
            'first_seen_at': str(data.get('first_seen_at') or '').strip(),
            'last_seen_at': str(data.get('last_seen_at') or '').strip(),
            'tags': data.get('tags') if isinstance(data.get('tags'), list) else [],
            'capabilities': data.get('capabilities') if isinstance(data.get('capabilities'), list) else [],
        }
    )

    target_idx = -1
    if target_id:
        for idx, existing in enumerate(repos):
            if str(existing.get('id') or '') == target_id:
                target_idx = idx
                break
    if target_idx < 0:
        for idx, existing in enumerate(repos):
            if str(existing.get('repo_link') or '').strip().lower() == repo_link.lower():
                target_idx = idx
                item['id'] = str(existing.get('id') or item['id'])
                item['created_at'] = str(existing.get('created_at') or item['created_at'])
                break

    if target_idx >= 0:
        repos[target_idx] = item
    else:
        repos.append(item)

    cfg['managed_install_repos'] = repos
    cfg['updated_at'] = utc_now()
    ok, write_err = write_json(CONFIG_PATH, cfg, mode=0o600)
    if not ok:
        return jsonify(ok=False, error='config_write_failed', detail=write_err), 500

    repos = sorted(repos, key=lambda current: str(current.get('name') or '').lower())
    return jsonify(ok=True, data={'item': item, 'repos': repos})


@bp_stream.post('/api/stream/player/repos/<repo_id>/delete')
def api_stream_player_repos_delete(repo_id: str):
    cfg = ensure_config()
    target_id = str(repo_id or '').strip()
    if not target_id:
        return jsonify(ok=False, error='repo_id_missing', detail='Repo-ID fehlt.'), 400

    repos = _managed_repos_from_config(cfg)
    filtered = [item for item in repos if str(item.get('id') or '').strip() != target_id]
    if len(filtered) == len(repos):
        return jsonify(ok=False, error='repo_not_found', detail='Repo nicht gefunden.'), 404

    cfg['managed_install_repos'] = filtered
    cfg['updated_at'] = utc_now()
    ok, write_err = write_json(CONFIG_PATH, cfg, mode=0o600)
    if not ok:
        return jsonify(ok=False, error='config_write_failed', detail=write_err), 500

    repos = sorted(filtered, key=lambda current: str(current.get('name') or '').lower())
    return jsonify(ok=True, data={'repos': repos})


@bp_stream.post('/api/stream/player/repos/<repo_id>/install-update')
def api_stream_player_repos_install_update(repo_id: str):
    cfg = ensure_config()
    target_id = str(repo_id or '').strip()
    repos = _managed_repos_from_config(cfg)
    target = next((item for item in repos if str(item.get('id') or '').strip() == target_id), None)
    if not target:
        return jsonify(ok=False, error='repo_not_found', detail='Repo nicht gefunden.'), 404
    if _is_remote_autodiscover_repo(target):
        host = str(target.get('hostname') or target.get('node_name') or 'remote').strip()
        return jsonify(
            ok=False,
            error='remote_repo_not_local',
            detail=f'Repo läuft auf Host "{host}" (Autodiscover). Install/Update ist nur lokal auf diesem Portal-Host möglich.',
        ), 409

    repo_link = str(target.get('repo_link') or '').strip()
    if not repo_link:
        return jsonify(ok=False, error='repo_link_missing', detail='Repo Link/Pfad fehlt.'), 400

    service_name = str(target.get('service_name') or '').strip() or _repo_default_service_name(repo_link)
    service_user = str(target.get('service_user') or '').strip()
    install_dir = str(target.get('install_dir') or '').strip()
    use_service = bool(target.get('use_service', True))
    autostart = bool(target.get('autostart', True))
    try:
        payload = player_update(
            repo_link,
            service_user=service_user,
            service_name=service_name,
            install_dir=install_dir,
            use_service=use_service,
            autostart=autostart,
        )
    except NetControlError as exc:
        status = 500 if exc.code in ('script_missing', 'execution_failed', 'player_update_failed') else 400
        return jsonify(ok=False, error=exc.code, detail=exc.detail or exc.message), status

    effective_service_name = str(payload.get('service_name') or service_name).strip() or service_name
    target['service_name'] = effective_service_name
    target['service_user'] = str(payload.get('service_user') or service_user).strip() or service_user
    target['install_dir'] = str(payload.get('install_dir') or install_dir).strip() or install_dir
    target['use_service'] = bool(payload.get('use_service') if payload.get('use_service') is not None else use_service)
    target['autostart'] = bool(payload.get('autostart') if payload.get('autostart') is not None else autostart)
    target['updated_at'] = utc_now()
    repos = [(_sanitize_managed_repo_entry(item) if str(item.get('id') or '').strip() == target_id else item) for item in repos]
    cfg['managed_install_repos'] = repos
    cfg['updated_at'] = utc_now()
    ok, write_err = write_json(CONFIG_PATH, cfg, mode=0o600)
    if not ok:
        return jsonify(ok=False, error='config_write_failed', detail=write_err), 500

    updated = next((item for item in repos if str(item.get('id') or '').strip() == target_id), _sanitize_managed_repo_entry(target))
    updated_out = dict(updated)
    updated_out['service_status'] = _managed_repo_service_status(updated_out)
    updated_out['service_status_checked_at'] = utc_now()
    payload_out = dict(payload)
    payload_out['service_name'] = effective_service_name
    payload_out['repo'] = updated_out
    return jsonify(ok=True, data=payload_out)


@bp_stream.post('/api/stream/player/repos/<repo_id>/service-autostart')
def api_stream_player_repos_service_autostart(repo_id: str):
    from app.core.netcontrol import player_service_autostart

    cfg = ensure_config()
    target_id = str(repo_id or '').strip()
    repos = _managed_repos_from_config(cfg)
    idx = next((i for i, item in enumerate(repos) if str(item.get('id') or '').strip() == target_id), -1)
    if idx < 0:
        return jsonify(ok=False, error='repo_not_found', detail='Repo nicht gefunden.'), 404

    data = request.get_json(force=True, silent=True) or {}
    enabled = bool(data.get('enabled', True))
    target = repos[idx]
    if _is_remote_autodiscover_repo(target):
        host = str(target.get('hostname') or target.get('node_name') or 'remote').strip()
        return jsonify(
            ok=False,
            error='remote_repo_not_local',
            detail=f'Repo läuft auf Host "{host}" (Autodiscover). Autostart kann hier nicht lokal gesetzt werden.',
        ), 409
    repo_link = str(target.get('repo_link') or '').strip()
    service_name = str(target.get('service_name') or '').strip() or _repo_default_service_name(repo_link)
    target['service_name'] = service_name

    try:
        payload = player_service_autostart(service_name=service_name, enabled=enabled)
    except NetControlError as exc:
        status = 500 if exc.code in ('script_missing', 'execution_failed', 'player_update_failed') else 400
        return jsonify(ok=False, error=exc.code, detail=exc.detail or exc.message), status

    target['autostart'] = enabled
    target['updated_at'] = utc_now()
    repos[idx] = _sanitize_managed_repo_entry(target)
    cfg['managed_install_repos'] = repos
    cfg['updated_at'] = utc_now()
    ok, write_err = write_json(CONFIG_PATH, cfg, mode=0o600)
    if not ok:
        return jsonify(ok=False, error='config_write_failed', detail=write_err), 500

    return jsonify(ok=True, data={'repo': repos[idx], 'action': payload})


@bp_stream.post('/api/stream/player/repos/<repo_id>/service-action')
def api_stream_player_repos_service_action(repo_id: str):
    cfg = ensure_config()
    target_id = str(repo_id or '').strip()
    repos = _managed_repos_from_config(cfg)
    idx = next((i for i, item in enumerate(repos) if str(item.get('id') or '').strip() == target_id), -1)
    if idx < 0:
        return jsonify(ok=False, error='repo_not_found', detail='Repo nicht gefunden.'), 404

    data = request.get_json(force=True, silent=True) or {}
    action = str(data.get('action') or '').strip().lower()
    if action not in {'start', 'stop', 'restart', 'status'}:
        return jsonify(ok=False, error='invalid_action', detail='Action must be start|stop|restart|status'), 400

    target = repos[idx]
    if _is_remote_autodiscover_repo(target):
        host = str(target.get('hostname') or target.get('node_name') or 'remote').strip()
        return jsonify(
            ok=False,
            error='remote_repo_not_local',
            detail=f'Repo läuft auf Host "{host}" (Autodiscover). Service-Aktionen sind hier nicht lokal möglich.',
        ), 409
    use_service = bool(target.get('use_service', True))
    if not use_service:
        return jsonify(ok=False, error='service_mode_disabled', detail='Repo läuft ohne Service-Modus.'), 400

    repo_link = str(target.get('repo_link') or '').strip()
    service_name = str(target.get('service_name') or '').strip() or _repo_default_service_name(repo_link)
    target['service_name'] = service_name
    try:
        payload = player_service_action(action, service_name)
    except NetControlError as exc:
        status = 500 if exc.code in ('script_missing', 'execution_failed', 'player_update_failed') else 400
        return jsonify(ok=False, error=exc.code, detail=exc.detail or exc.message), status

    target['updated_at'] = utc_now()
    repos[idx] = _sanitize_managed_repo_entry(target)
    cfg['managed_install_repos'] = repos
    cfg['updated_at'] = utc_now()
    ok, write_err = write_json(CONFIG_PATH, cfg, mode=0o600)
    if not ok:
        return jsonify(ok=False, error='config_write_failed', detail=write_err), 500

    repo_out = dict(repos[idx])
    repo_out['service_status'] = _managed_repo_service_status(repo_out)
    return jsonify(ok=True, data={'repo': repo_out, 'action': payload})


@bp_stream.post('/api/stream/player/repos/<repo_id>/uninstall')
def api_stream_player_repos_uninstall(repo_id: str):
    from app.core.netcontrol import player_uninstall

    cfg = ensure_config()
    target_id = str(repo_id or '').strip()
    repos = _managed_repos_from_config(cfg)
    idx = next((i for i, item in enumerate(repos) if str(item.get('id') or '').strip() == target_id), -1)
    if idx < 0:
        return jsonify(ok=False, error='repo_not_found', detail='Repo nicht gefunden.'), 404

    data = request.get_json(force=True, silent=True) or {}
    remove_repo = bool(data.get('remove_repo', False))
    target = repos[idx]
    if _is_remote_autodiscover_repo(target):
        host = str(target.get('hostname') or target.get('node_name') or 'remote').strip()
        return jsonify(
            ok=False,
            error='remote_repo_not_local',
            detail=f'Repo läuft auf Host "{host}" (Autodiscover). Deinstallation muss auf diesem Host erfolgen.',
        ), 409
    repo_link = str(target.get('repo_link') or '').strip()
    install_dir = str(target.get('install_dir') or '').strip()
    service_user = str(target.get('service_user') or '').strip()
    service_name = str(target.get('service_name') or '').strip() or _repo_default_service_name(repo_link)
    target['service_name'] = service_name

    try:
        payload = player_uninstall(
            repo_link=repo_link,
            service_user=service_user,
            service_name=service_name,
            install_dir=install_dir,
            remove_repo=remove_repo,
        )
    except NetControlError as exc:
        status = 500 if exc.code in ('script_missing', 'execution_failed', 'player_update_failed') else 400
        return jsonify(ok=False, error=exc.code, detail=exc.detail or exc.message), status

    return jsonify(ok=True, data={'repo': target, 'action': payload})


@bp_stream.post('/api/stream/player/install-update')
def api_stream_player_install_update():
    cfg = ensure_config()
    data = request.get_json(force=True, silent=True) or {}
    repo_link = str(data.get('player_repo_link') or data.get('player_repo_dir') or cfg.get('player_repo_link') or cfg.get('player_repo_dir') or '').strip()
    service_name = str(data.get('player_service_name') or cfg.get('player_service_name') or STREAM_SERVICE_NAME).strip() or STREAM_SERVICE_NAME
    service_user = str(data.get('player_service_user') or cfg.get('player_service_user') or '').strip()
    install_dir = str(data.get('player_install_dir') or cfg.get('player_install_dir') or '').strip()
    use_service = bool(data.get('use_service', True))
    autostart = bool(data.get('autostart', True))

    if not repo_link:
        return jsonify(ok=False, error='player_repo_missing', detail='Bitte Player-Repo-Link oder lokalen Pfad setzen.'), 400

    try:
        payload = player_update(
            repo_link,
            service_user=service_user,
            service_name=service_name,
            install_dir=install_dir,
            use_service=use_service,
            autostart=autostart,
        )
    except NetControlError as exc:
        status = 500 if exc.code in ('script_missing', 'execution_failed', 'player_update_failed') else 400
        return jsonify(ok=False, error=exc.code, detail=exc.detail or exc.message), status

    return jsonify(ok=True, data=payload)


@bp_stream.get('/api/stream/player/install-update/status')
def api_stream_player_install_update_status():
    job_id = str(request.args.get('job_id') or '').strip()
    try:
        payload = player_update_status(job_id=job_id)
    except NetControlError as exc:
        status = 500 if exc.code in ('script_missing', 'execution_failed', 'update_state_read_failed') else 400
        return jsonify(ok=False, error=exc.code, detail=exc.detail or exc.message), status
    return jsonify(ok=True, data=payload)


@bp_stream.get('/api/stream/player/audio/status')
def api_stream_player_audio_status():
    code, payload, err = _player_control_request('GET', '/player/status', timeout=6)
    if code is None:
        return jsonify(ok=False, error='audio_control_unreachable', detail=err), 502
    if code >= 400:
        return jsonify(ok=False, error='audio_status_failed', detail=f'HTTP {code}', response=payload), (code if code >= 400 else 502)
    return jsonify(ok=True, data=payload)


@bp_stream.get('/api/stream/player/audio/files')
def api_stream_player_audio_files():
    requested_path = str(request.args.get('path') or '').strip()
    root = _resolve_audio_allowed_root()
    if requested_path:
        try:
            browser_root = _audio_path_browser_root()
            candidate = Path(requested_path).expanduser().resolve()
            if not candidate.exists() or not candidate.is_dir():
                return jsonify(ok=False, error='invalid_path', detail='Verzeichnis existiert nicht.'), 400
            if not candidate.is_relative_to(browser_root):
                return jsonify(ok=False, error='invalid_path', detail='Pfad liegt außerhalb von /mnt.'), 400
            root = candidate
        except Exception:
            return jsonify(ok=False, error='invalid_path', detail='Ungültiger Audio-Pfad.'), 400
    if not root.exists() or not root.is_dir():
        return jsonify(ok=True, root=str(root), current_path=str(root), files=[])

    allowed_ext = {'.mp3', '.ogg', '.wav', '.flac', '.m4a', '.aac'}
    files: list[dict] = []
    for path in sorted(root.rglob('*')):
        if not path.is_file():
            continue
        if path.suffix.lower() not in allowed_ext:
            continue
        try:
            rel = str(path.relative_to(root))
        except Exception:
            rel = path.name
        files.append(
            {
                'name': path.name,
                'relative_path': rel,
                'path': str(path.resolve()),
                'size_bytes': int(path.stat().st_size or 0),
            }
        )
    return jsonify(ok=True, root=str(root), current_path=str(root), files=files)


@bp_stream.get('/api/stream/player/audio/path-browser')
def api_stream_player_audio_path_browser():
    try:
        root = _audio_path_browser_root()
        current = _audio_path_browser_resolve(str(request.args.get('path') or ''), root)
    except RuntimeError as exc:
        return jsonify(ok=False, error='invalid_path', detail=str(exc)), 400

    allowed_ext = {'.mp3', '.ogg', '.wav', '.flac', '.m4a', '.aac'}
    directories: list[dict] = []
    files: list[dict] = []
    try:
        for child in sorted(current.iterdir(), key=lambda item: item.name.lower()):
            name = child.name
            if name.startswith('.'):
                continue
            try:
                if child.is_dir():
                    directories.append({'name': name, 'path': str(child.resolve())})
                    continue
                if not child.is_file():
                    continue
            except Exception:
                continue
            if child.suffix.lower() not in allowed_ext:
                continue
            size_bytes = 0
            try:
                size_bytes = int(child.stat().st_size or 0)
            except Exception:
                size_bytes = 0
            files.append({'name': name, 'path': str(child.resolve()), 'size_bytes': size_bytes})
    except Exception as exc:
        return jsonify(ok=False, error='path_browser_failed', detail=str(exc)), 500

    parent_path = ''
    if current != root:
        parent_candidate = current.parent.resolve()
        try:
            if parent_candidate.is_relative_to(root):
                parent_path = str(parent_candidate)
            else:
                parent_path = str(root)
        except Exception:
            parent_path = str(root)

    return jsonify(
        ok=True,
        data={
            'current_path': str(current),
            'root_path': str(root),
            'parent_path': parent_path,
            'directories': directories,
            'files': files,
        },
    )


@bp_stream.post('/api/stream/player/audio/play-file')
def api_stream_player_audio_play_file():
    data = request.get_json(force=True, silent=True) or {}
    path = str(data.get('path') or '').strip()
    if not path:
        return jsonify(ok=False, error='missing_path', detail='path fehlt.'), 400
    code, payload, err = _player_control_request('POST', '/player/play-file', {'path': path}, timeout=8)
    if code is None:
        return jsonify(ok=False, error='audio_control_unreachable', detail=err), 502
    return jsonify(ok=code < 400 and bool(payload.get('ok', True)), data=payload), (code if code >= 400 else 200)


@bp_stream.post('/api/stream/player/audio/play-stream')
def api_stream_player_audio_play_stream():
    data = request.get_json(force=True, silent=True) or {}
    url = str(data.get('url') or '').strip()
    if not url:
        return jsonify(ok=False, error='missing_url', detail='url fehlt.'), 400
    code, payload, err = _player_control_request('POST', '/player/play-stream', {'url': url}, timeout=8)
    if code is None:
        return jsonify(ok=False, error='audio_control_unreachable', detail=err), 502
    return jsonify(ok=code < 400 and bool(payload.get('ok', True)), data=payload), (code if code >= 400 else 200)


@bp_stream.post('/api/stream/player/audio/stop')
def api_stream_player_audio_stop():
    code, payload, err = _player_control_request('POST', '/player/stop', {}, timeout=8)
    if code is None:
        return jsonify(ok=False, error='audio_control_unreachable', detail=err), 502
    return jsonify(ok=code < 400 and bool(payload.get('ok', True)), data=payload), (code if code >= 400 else 200)


@bp_stream.post('/api/stream/player/audio/pause')
def api_stream_player_audio_pause():
    code, payload, err = _player_control_request('POST', '/player/pause', {}, timeout=8)
    if code is None:
        return jsonify(ok=False, error='audio_control_unreachable', detail=err), 502
    return jsonify(ok=code < 400 and bool(payload.get('ok', True)), data=payload), (code if code >= 400 else 200)


@bp_stream.post('/api/stream/player/audio/resume')
def api_stream_player_audio_resume():
    code, payload, err = _player_control_request('POST', '/player/resume', {}, timeout=8)
    if code is None:
        return jsonify(ok=False, error='audio_control_unreachable', detail=err), 502
    return jsonify(ok=code < 400 and bool(payload.get('ok', True)), data=payload), (code if code >= 400 else 200)


@bp_stream.post('/api/stream/player/audio/volume')
def api_stream_player_audio_volume():
    data = request.get_json(force=True, silent=True) or {}
    if 'volume' not in data:
        return jsonify(ok=False, error='missing_volume', detail='volume fehlt.'), 400
    try:
        volume = int(data.get('volume'))
    except Exception:
        return jsonify(ok=False, error='invalid_volume', detail='volume muss int sein.'), 400
    code, payload, err = _player_control_request('POST', '/player/volume', {'volume': volume}, timeout=8)
    if code is None:
        return jsonify(ok=False, error='audio_control_unreachable', detail=err), 502
    return jsonify(ok=code < 400 and bool(payload.get('ok', True)), data=payload), (code if code >= 400 else 200)
