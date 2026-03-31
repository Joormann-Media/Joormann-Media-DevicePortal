from __future__ import annotations

import json
import hashlib
import os
import shutil
import fcntl
from datetime import datetime
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
    return jsonify(
        ok=True,
        config={
            'player_repo_link': repo_link,
            'player_service_name': str(cfg.get('player_service_name') or STREAM_SERVICE_NAME),
            'player_service_user': str(cfg.get('player_service_user') or ''),
        },
    )


@bp_stream.post('/api/stream/player/repo')
def api_stream_player_repo_set():
    cfg = ensure_config()
    data = request.get_json(force=True, silent=True) or {}
    repo_link = str(data.get('player_repo_link') or data.get('player_repo_dir') or '').strip()
    service_name = str(data.get('player_service_name') or STREAM_SERVICE_NAME).strip() or STREAM_SERVICE_NAME
    service_user = str(data.get('player_service_user') or '').strip()

    cfg['player_repo_link'] = repo_link
    cfg['player_repo_dir'] = repo_link
    cfg['player_service_name'] = service_name
    cfg['player_service_user'] = service_user
    cfg['updated_at'] = utc_now()
    ok, write_err = write_json(CONFIG_PATH, cfg, mode=0o600)
    if not ok:
        return jsonify(ok=False, error='config_write_failed', detail=write_err), 500

    return jsonify(ok=True, config={
        'player_repo_link': repo_link,
        'player_service_name': service_name,
        'player_service_user': service_user,
    })


@bp_stream.post('/api/stream/player/install-update')
def api_stream_player_install_update():
    cfg = ensure_config()
    data = request.get_json(force=True, silent=True) or {}
    repo_link = str(data.get('player_repo_link') or data.get('player_repo_dir') or cfg.get('player_repo_link') or cfg.get('player_repo_dir') or '').strip()
    service_name = str(data.get('player_service_name') or cfg.get('player_service_name') or STREAM_SERVICE_NAME).strip() or STREAM_SERVICE_NAME
    service_user = str(data.get('player_service_user') or cfg.get('player_service_user') or '').strip()

    if not repo_link:
        return jsonify(ok=False, error='player_repo_missing', detail='Bitte Player-Repo-Link oder lokalen Pfad setzen.'), 400

    try:
        payload = player_update(repo_link, service_user=service_user, service_name=service_name)
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
    root = _resolve_audio_allowed_root()
    if not root.exists() or not root.is_dir():
        return jsonify(ok=True, root=str(root), files=[])

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
    return jsonify(ok=True, root=str(root), files=files)


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
