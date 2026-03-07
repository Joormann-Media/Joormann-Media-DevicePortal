from __future__ import annotations

import hmac
from urllib.parse import urlencode

from flask import Blueprint, jsonify, request

from app.core.config import _panel_url, _safe_base_url, ensure_config
from app.core.display import get_display_snapshot
from app.core.device import ensure_device
from app.core.fingerprint import collect_fingerprint, ensure_fingerprint
from app.core.gitinfo import get_update_info
from app.core.httpclient import http_get_json, http_get_text, http_post_json
from app.core.jsonio import write_json
from app.core.netcontrol import NetControlError, get_ap_clients, get_ap_status, get_network_info, get_wifi_status
from app.core.paths import CONFIG_PATH
from app.core.state import update_state
from app.core.storage_state import get_storage_state
from app.core.systeminfo import get_hostname, get_ip, parse_load_stats, parse_mem_stats_kb
from app.core.timeutil import utc_now

bp_panel = Blueprint('panel', __name__)


def _update_panel_link_state(cfg: dict, **kwargs) -> dict:
    st = cfg.get('panel_link_state') if isinstance(cfg.get('panel_link_state'), dict) else {}
    st.setdefault('linked', False)
    st.setdefault('last_check', None)
    st.setdefault('last_http', None)
    st.setdefault('last_response', None)
    st.setdefault('last_error', '')
    st.update(kwargs)
    st['last_check'] = utc_now()
    cfg['panel_link_state'] = st
    cfg['updated_at'] = utc_now()
    write_json(CONFIG_PATH, cfg, mode=0o600)
    return st


def _current_linked(cfg: dict) -> bool:
    st = cfg.get('panel_link_state') if isinstance(cfg.get('panel_link_state'), dict) else {}
    return bool(st.get('linked'))


def _response_indicates_unlinked(code: int | None, resp: object) -> bool:
    if code in (401, 403, 404, 410):
        return True
    if not isinstance(resp, dict):
        return False

    parts: list[str] = []
    for key in ('error', 'code', 'status', 'message', 'detail', 'reason'):
        value = resp.get(key)
        if value is not None:
            parts.append(str(value).strip().lower())

    text = ' '.join(parts)
    markers = (
        'unlinked',
        'not_linked',
        'not linked',
        'device_not_found',
        'unknown_device',
        'unknown device',
        'device missing',
        'auth_invalid',
        'invalid_auth',
        'invalid auth',
        'auth key invalid',
        'authkey invalid',
        'revoked',
    )
    return any(marker in text for marker in markers)


def _sticky_linked(cfg: dict, next_linked: bool, code: int | None = None, resp: object = None) -> bool:
    if next_linked:
        return True
    if _response_indicates_unlinked(code, resp):
        return False
    return _current_linked(cfg)


def _panel_ping_payload(dev: dict, fp: dict, host: str, ip: str) -> dict:
    return {
        'deviceUuid': dev.get('device_uuid') or '',
        'authKey': dev.get('auth_key') or '',
        'hostname': host,
        'ipAddress': ip,
        'fingerprint': {
            'machineId': dev.get('machine_id') or fp.get('machine_id') or '',
            'piSerial': dev.get('pi_serial') or ((fp.get('cpu') or {}).get('serial') if isinstance(fp.get('cpu'), dict) else ''),
            'os': fp.get('os') or {},
            'kernel': fp.get('kernel') or '',
        },
    }


def _panel_register_payload(cfg: dict, dev: dict, fp: dict, host: str, ip: str, token: str) -> dict:
    payload = _panel_ping_payload(dev, fp, host, ip)
    payload['registrationToken'] = token
    payload['panelRegisterPath'] = cfg.get('panel_register_path')
    # Panel register DTO expects these fields at top-level (not only nested in fingerprint).
    pi_serial = (
        (dev.get('pi_serial') or '').strip()
        or (((fp.get('cpu') or {}).get('serial') if isinstance(fp.get('cpu'), dict) else '') or '').strip()
        or f"unknown-{(dev.get('device_uuid') or 'device').replace('-', '')[:8]}"
    )
    machine_id = (
        (dev.get('machine_id') or '').strip()
        or (fp.get('machine_id') or '').strip()
        or f"unknown-{(dev.get('device_uuid') or 'device').replace('-', '')[:8]}"
    )
    payload['piSerial'] = pi_serial
    payload['machineId'] = machine_id
    snapshots = _collect_runtime_snapshots(cfg, dev, fp, host, ip)
    payload.update(snapshots)
    return payload


def _panel_sync_payload(cfg: dict, dev: dict, fp: dict, host: str, ip: str) -> dict:
    payload = _panel_ping_payload(dev, fp, host, ip)
    payload['panelRegisterPath'] = cfg.get('panel_register_path')
    pi_serial = (
        (dev.get('pi_serial') or '').strip()
        or (((fp.get('cpu') or {}).get('serial') if isinstance(fp.get('cpu'), dict) else '') or '').strip()
        or f"unknown-{(dev.get('device_uuid') or 'device').replace('-', '')[:8]}"
    )
    machine_id = (
        (dev.get('machine_id') or '').strip()
        or (fp.get('machine_id') or '').strip()
        or f"unknown-{(dev.get('device_uuid') or 'device').replace('-', '')[:8]}"
    )
    payload['piSerial'] = pi_serial
    payload['machineId'] = machine_id
    snapshots = _collect_runtime_snapshots(cfg, dev, fp, host, ip)
    payload.update(snapshots)
    return payload


def _safe_call(default: object, fn):
    try:
        value = fn()
        if value is None:
            return default
        return value
    except Exception:
        return default


def _software_snapshot(update_info: dict, network_info: dict, storage_info: dict) -> list[dict]:
    tailscale = (network_info.get('tailscale') if isinstance(network_info, dict) else {}) or {}
    tailscale_present = bool(tailscale.get('present'))
    tailscale_ip = str(tailscale.get('ip') or '').strip()
    local_version = str(update_info.get('local_version') or '').strip()
    local_commit = str(update_info.get('local_commit') or '').strip()
    remote_commit = str(update_info.get('remote_commit') or '').strip()
    items = [
        {
            'name': 'DevicePortal',
            'component': 'deviceportal',
            'category': 'portal',
            'type': 'git',
            'status': 'installed',
            'version': local_version or local_commit[:12],
            'source': 'portal',
            'notes': 'local branch: ' + str(update_info.get('local_branch') or '').strip() + (f' | commit: {local_commit[:12]}' if local_commit else ''),
        },
        {
            'name': 'Portal Update',
            'component': 'portal-update',
            'category': 'update',
            'type': 'git',
            'status': 'update_available' if bool(update_info.get('available')) else ('unknown' if str(update_info.get('error') or '').strip() else 'up_to_date'),
            'version': remote_commit[:12] or local_commit[:12],
            'source': 'origin',
            'notes': str(update_info.get('error') or '').strip(),
        },
        {
            'name': 'Tailscale',
            'component': 'tailscale',
            'category': 'network',
            'type': 'apt',
            'status': 'connected' if tailscale_present and tailscale_ip else ('installed' if tailscale_present else 'missing'),
            'version': tailscale_ip or '',
            'source': 'network_info',
            'notes': '',
        },
        {
            'name': 'Storage Service',
            'component': 'storage',
            'category': 'storage',
            'type': 'managed',
            'status': 'active' if int(storage_info.get('known_count') or 0) > 0 else 'idle',
            'version': str(storage_info.get('known_count') or 0),
            'source': 'portal',
            'notes': '',
        },
    ]
    return items


def _storage_snapshot(storage_info: dict) -> dict:
    drives = storage_info.get('drives')
    internal = storage_info.get('internal')
    out_devices: list[dict] = []

    if isinstance(drives, list):
        for item in drives:
            if not isinstance(item, dict):
                continue
            out_devices.append(
                {
                    'name': item.get('drive_name') or item.get('name') or item.get('label') or item.get('id') or '',
                    'driveName': item.get('drive_name') or '',
                    'uuid': item.get('uuid') or '',
                    'partUuid': item.get('part_uuid') or '',
                    'label': item.get('label') or '',
                    'filesystem': item.get('filesystem') or '',
                    'sizeBytes': int(item.get('size_bytes') or item.get('total_bytes') or 0),
                    'total_bytes': int(item.get('total_bytes') or 0),
                    'used_bytes': int(item.get('used_bytes') or 0),
                    'free_bytes': int(item.get('free_bytes') or 0),
                    'used_percent': int(item.get('used_percent') or 0),
                    'devicePath': item.get('current_device_path') or item.get('last_seen_device_path') or '',
                    'mountPath': item.get('mount_path') or item.get('current_mount_path') or '',
                    'mountOptions': item.get('mount_options') or '',
                    'isMounted': bool(item.get('mounted')),
                    'isPresent': bool(item.get('present')),
                    'isEnabled': bool(item.get('is_enabled', True)),
                    'allowMediaStorage': bool(item.get('allow_media_storage', False)),
                    'allowPortalStorage': bool(item.get('allow_portal_storage', False)),
                    'vendor': item.get('vendor') or '',
                    'model': item.get('model') or '',
                    'serial': item.get('serial') or '',
                    'transport': item.get('transport') or '',
                    'state': item.get('state') or '',
                    'is_internal': bool(item.get('is_internal', False)),
                }
            )

    if isinstance(internal, dict):
        out_devices.append(
            {
                'name': internal.get('drive_name') or internal.get('id') or 'internal-media',
                'driveName': internal.get('drive_name') or 'internal-media',
                'uuid': '',
                'partUuid': '',
                'label': internal.get('drive_name') or 'internal-media',
                'filesystem': internal.get('filesystem') or internal.get('expected_filesystem') or '',
                'sizeBytes': int(internal.get('loop_total_bytes') or internal.get('total_bytes') or 0),
                'total_bytes': int(internal.get('loop_total_bytes') or internal.get('total_bytes') or 0),
                'used_bytes': int(internal.get('loop_used_bytes') or internal.get('used_bytes') or 0),
                'free_bytes': int(internal.get('loop_free_bytes') or internal.get('free_bytes') or 0),
                'used_percent': int(internal.get('loop_used_percent') or internal.get('used_percent') or 0),
                'devicePath': internal.get('mounted_source') or internal.get('image_path') or '',
                'mountPath': internal.get('mount_path') or '',
                'mountOptions': '',
                'isMounted': bool(internal.get('mounted')),
                'isPresent': bool(internal.get('present')),
                'isEnabled': bool(internal.get('enabled', True)),
                'allowMediaStorage': bool(internal.get('allow_media_storage', True)),
                'allowPortalStorage': bool(internal.get('allow_portal_storage', True)),
                'vendor': '',
                'model': '',
                'serial': '',
                'transport': 'internal_loop',
                'state': internal.get('state') or '',
                'is_internal': True,
            }
        )

    return {
        'known_count': int(storage_info.get('known_count') or 0),
        'present_count': int(storage_info.get('present_count') or 0),
        'mounted_count': int(storage_info.get('mounted_count') or 0),
        'drives': drives if isinstance(drives, list) else [],
        'internal': internal if isinstance(internal, dict) else {},
        'devices': out_devices,
    }


def _collect_runtime_snapshots(cfg: dict, dev: dict, fp: dict, host: str, ip: str) -> dict:
    net_info = _safe_call({}, get_network_info)
    wifi_status = _safe_call({}, lambda: get_wifi_status(ifname='wlan0'))
    ap_status = _safe_call({}, lambda: get_ap_status(ifname='wlan0', profile='jm-hotspot'))
    ap_clients = _safe_call({'clients': []}, lambda: get_ap_clients(ifname='wlan0'))
    storage_info = _safe_call({}, get_storage_state)
    update_info = _safe_call({}, get_update_info)
    display_info = _safe_call({}, lambda: get_display_snapshot(cfg))
    memory = _safe_call({}, parse_mem_stats_kb)
    load = _safe_call({}, parse_load_stats)

    def _cpu_temp_c() -> float | None:
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r', encoding='utf-8') as f:
                raw = (f.read() or '').strip()
            if raw and raw.lstrip('-').isdigit():
                return round(int(raw) / 1000.0, 1)
        except Exception:
            pass
        return None

    def _uptime_seconds() -> int | None:
        try:
            with open('/proc/uptime', 'r', encoding='utf-8') as f:
                first = (f.read() or '').split()[0]
            return int(float(first))
        except Exception:
            return None

    def _uptime_human(seconds: int | None) -> str:
        if seconds is None or seconds < 0:
            return ''
        days, rem = divmod(seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, _ = divmod(rem, 60)
        parts: list[str] = []
        if days:
            parts.append(f'{days}d')
        if hours or days:
            parts.append(f'{hours}h')
        parts.append(f'{minutes}m')
        return ' '.join(parts)

    mem_total_kb = int(memory.get('mem_total_kb') or 0) if isinstance(memory, dict) else 0
    mem_free_kb = int(memory.get('mem_free_kb') or 0) if isinstance(memory, dict) else 0
    mem_avail_kb = int(memory.get('mem_available_kb') or 0) if isinstance(memory, dict) else 0
    used_kb = max(0, mem_total_kb - (mem_avail_kb or mem_free_kb))
    cpu_percent = None
    if isinstance(load, dict):
        raw_percent = load.get('cpu_percent_estimate')
        if isinstance(raw_percent, (int, float)):
            cpu_percent = float(raw_percent)

    uptime_sec = _uptime_seconds()
    cpu_temp = _cpu_temp_c()

    identity = {
        'deviceUuid': dev.get('device_uuid') or '',
        'machineId': dev.get('machine_id') or fp.get('machine_id') or '',
        'piSerial': dev.get('pi_serial') or ((fp.get('cpu') or {}).get('serial') if isinstance(fp.get('cpu'), dict) else ''),
        'hostname': host,
        'ipAddress': ip,
        'os': fp.get('os') if isinstance(fp.get('os'), dict) else {},
        'kernel': fp.get('kernel') or '',
        'cpuModel': ((fp.get('cpu') or {}).get('model') if isinstance(fp.get('cpu'), dict) else '') or '',
        'fingerprint': fp,
        'displayCount': int(((display_info.get('display_summary') or {}).get('total') if isinstance(display_info, dict) and isinstance(display_info.get('display_summary'), dict) else 0) or 0),
    }

    network = {
        'network_info': net_info if isinstance(net_info, dict) else {},
        'wifi': wifi_status if isinstance(wifi_status, dict) else {},
        'ap': {
            'active': bool((ap_status or {}).get('active')) if isinstance(ap_status, dict) else False,
            'status': ap_status if isinstance(ap_status, dict) else {},
            'clients': ap_clients.get('clients') if isinstance(ap_clients, dict) and isinstance(ap_clients.get('clients'), list) else [],
            'clients_count': len(ap_clients.get('clients') or []) if isinstance(ap_clients, dict) and isinstance(ap_clients.get('clients'), list) else 0,
        },
        'tailscale': ((net_info.get('tailscale') if isinstance(net_info, dict) else {}) or {}),
    }

    storage = _storage_snapshot(storage_info if isinstance(storage_info, dict) else {})
    software = _software_snapshot(update_info if isinstance(update_info, dict) else {}, net_info if isinstance(net_info, dict) else {}, storage_info if isinstance(storage_info, dict) else {})

    return {
        'panelBaseUrl': _safe_base_url(cfg.get('admin_base_url', '')),
        'identity': identity,
        'health': {
            'cpu': {
                'load_1m': load.get('load_1m') if isinstance(load, dict) else None,
                'load_5m': load.get('load_5m') if isinstance(load, dict) else None,
                'load_15m': load.get('load_15m') if isinstance(load, dict) else None,
                'cpu_cores': load.get('cpu_cores') if isinstance(load, dict) else None,
                'cpu_percent_estimate': cpu_percent,
                'temperature_c': cpu_temp,
            },
            'memory': memory if isinstance(memory, dict) else {},
            'memory_usage': {
                'used_kb': used_kb if mem_total_kb > 0 else None,
                'total_kb': mem_total_kb if mem_total_kb > 0 else None,
                'used_mb': round((used_kb / 1024.0), 1) if mem_total_kb > 0 else None,
                'total_mb': round((mem_total_kb / 1024.0), 1) if mem_total_kb > 0 else None,
                'used_percent': round((used_kb / mem_total_kb) * 100.0, 1) if mem_total_kb > 0 else None,
            },
            'load': load if isinstance(load, dict) else {},
            'uptime_seconds': uptime_sec,
            'uptime_human': _uptime_human(uptime_sec),
            'observedAt': utc_now(),
        },
        'network': network,
        'storage': storage,
        'display': display_info if isinstance(display_info, dict) else {},
        'displays': (display_info.get('displays') if isinstance(display_info, dict) and isinstance(display_info.get('displays'), list) else []),
        'primaryDisplay': (display_info.get('primary_display') if isinstance(display_info, dict) and isinstance(display_info.get('primary_display'), dict) else {}),
        'displaySummary': (display_info.get('display_summary') if isinstance(display_info, dict) and isinstance(display_info.get('display_summary'), dict) else {}),
        'software': software,
        'portal': {
            'update': update_info if isinstance(update_info, dict) else {},
            'linkState': cfg.get('panel_link_state') if isinstance(cfg.get('panel_link_state'), dict) else {},
            'registerPath': cfg.get('panel_register_path') or '',
            'pingPath': cfg.get('panel_ping_path') or '',
        },
    }


def _is_truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'ok', 'valid', 'linked', 'success')
    return False


def _panel_handshake_url(base_url: str) -> str:
    return f"{base_url}/api/device/link/handshake"


def _extract_handshake_register_path(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ''
    direct = str(payload.get('registerPath') or payload.get('register_path') or '').strip()
    if direct:
        return direct
    data = payload.get('data')
    if isinstance(data, dict):
        nested = str(data.get('registerPath') or data.get('register_path') or '').strip()
        if nested:
            return nested
    return ''


def _handshake_schema_ok(payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    if _extract_handshake_register_path(payload):
        return True
    if any(key in payload for key in ('ok', 'success', 'message', 'data', 'version')):
        return True
    data = payload.get('data')
    if isinstance(data, dict) and any(key in data for key in ('ok', 'success', 'message', 'version', 'registerPath', 'register_path')):
        return True
    return False


def _probe_panel_register_route(base_url: str, register_path: str) -> tuple[int | None, str]:
    path = (register_path or '/api/device/link/register').strip()
    if not path.startswith('/'):
        path = f'/{path}'
    probe_url = f"{base_url}{path}"
    code, _, err = http_get_text(probe_url, timeout=6)
    return code, err


def _panel_verify_token_url(base_url: str) -> str:
    return f"{base_url}/api/device/link/verify-token"


def _panel_assign_url(base_url: str) -> str:
    return f"{base_url}/api/device/link/assign"


def _panel_sync_status_url(base_url: str) -> str:
    return f"{base_url}/api/device/link/sync-status"


def _response_indicates_success(code: int | None, resp: dict | None) -> bool:
    if code is None:
        return False
    if code < 200 or code >= 300:
        return False
    if not isinstance(resp, dict):
        return True
    for key in ('ok', 'success', 'valid', 'linked', 'registered', 'assigned'):
        if key in resp:
            return _is_truthy(resp.get(key))
    data = resp.get('data')
    if isinstance(data, dict):
        for key in ('ok', 'success', 'valid', 'linked', 'registered', 'assigned'):
            if key in data:
                return _is_truthy(data.get(key))
        if data.get('deviceId') or data.get('deviceSlug'):
            return True
    # If backend returned non-JSON fallback payload, do not treat it as success.
    if isinstance(resp, dict) and isinstance(resp.get('raw'), str):
        return False
    if resp.get('deviceId') or resp.get('deviceSlug'):
        return True
    return True


def _extract_response_message(resp: dict | None) -> str:
    if not isinstance(resp, dict):
        return ''
    candidates: list[object] = [
        resp.get('message'),
        resp.get('error'),
        resp.get('detail'),
        resp.get('error_code'),
        resp.get('raw'),
        (resp.get('data') or {}).get('message') if isinstance(resp.get('data'), dict) else '',
        (resp.get('data') or {}).get('detail') if isinstance(resp.get('data'), dict) else '',
        (resp.get('data') or {}).get('error') if isinstance(resp.get('data'), dict) else '',
        (resp.get('data') or {}).get('error_code') if isinstance(resp.get('data'), dict) else '',
    ]
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    raw = resp.get('raw') if isinstance(resp, dict) else ''
    if isinstance(raw, str) and raw.strip():
        lowered = raw.lower()
        marker = 'no route found for'
        if marker in lowered:
            idx = lowered.find(marker)
            excerpt = raw[idx:idx + 220].replace('\n', ' ').replace('\r', ' ')
            return excerpt.strip()
        return raw[:220].replace('\n', ' ').replace('\r', ' ').strip()
    return ''


def _extract_items(resp: dict | None) -> list[dict]:
    if not isinstance(resp, dict):
        return []
    direct_candidates = (
        resp.get('items'),
        resp.get('results'),
        resp.get('users'),
        resp.get('customers'),
    )
    for candidate in direct_candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
    data = resp.get('data')
    if isinstance(data, dict):
        for key in ('items', 'results', 'users', 'customers'):
            candidate = data.get(key)
            if isinstance(candidate, list):
                return [item for item in candidate if isinstance(item, dict)]
    return []


def _extract_panel_device_flags(resp: dict | None) -> dict | None:
    if not isinstance(resp, dict):
        return None

    def _coerce_bool(value: object) -> bool | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ('1', 'true', 'yes', 'on', 'active', 'locked'):
                return True
            if lowered in ('0', 'false', 'no', 'off', 'inactive', 'unlocked'):
                return False
        return None

    for source in (resp, (resp.get('data') if isinstance(resp.get('data'), dict) else None)):
        if not isinstance(source, dict):
            continue
        is_active = _coerce_bool(source.get('isActive', source.get('is_active')))
        is_locked = _coerce_bool(source.get('isLocked', source.get('is_locked')))
        if is_active is None and is_locked is None:
            continue
        return {
            'is_active': is_active,
            'is_locked': is_locked,
            'updated_at': utc_now(),
        }

    return None


def _portal_auth_valid(data: dict, dev: dict) -> tuple[bool, str]:
    uuid_in = str(data.get('deviceUuid') or data.get('device_uuid') or '').strip()
    auth_in = str(data.get('authKey') or data.get('auth_key') or '').strip()
    uuid_ref = str(dev.get('device_uuid') or '').strip()
    auth_ref = str(dev.get('auth_key') or '').strip()

    if not uuid_in or not auth_in:
        return False, 'device_auth_missing'
    if not uuid_ref or not auth_ref:
        return False, 'device_auth_unavailable'
    if not hmac.compare_digest(uuid_in, uuid_ref):
        return False, 'device_uuid_invalid'
    if not hmac.compare_digest(auth_in, auth_ref):
        return False, 'auth_key_invalid'
    return True, ''


def _normalize_search_items(items: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for item in items:
        ident = str(item.get('id') or item.get('uuid') or item.get('slug') or '').strip()
        if not ident:
            continue
        name = str(item.get('name') or item.get('fullName') or item.get('displayName') or '').strip()
        if not name:
            first = str(item.get('firstName') or item.get('first_name') or '').strip()
            last = str(item.get('lastName') or item.get('last_name') or '').strip()
            name = " ".join(part for part in (first, last) if part).strip() or ident
        subtitle = str(item.get('email') or item.get('company') or item.get('slug') or '').strip()
        normalized.append(
            {
                'id': ident,
                'name': name,
                'subtitle': subtitle,
                'raw': item,
            }
        )
    return normalized


@bp_panel.post('/api/panel/test-url')
def api_panel_test_url():
    data = request.get_json(force=True, silent=True) or {}
    raw = (data.get('url') or '').strip()
    if not raw:
        return jsonify(ok=False, error='Missing url'), 400

    base = _safe_base_url(raw)
    code, _, err = http_get_text(base, timeout=5)
    if code is None:
        return jsonify(ok=False, error=f'URL unreachable: {err}'), 400
    handshake_url = _panel_handshake_url(base)
    hs_code, hs_resp, hs_err = http_get_json(handshake_url, timeout=6)
    if hs_code is None:
        return jsonify(
            ok=False,
            error='panel_handshake_unreachable',
            detail=str(hs_err),
            base_url=base,
            handshake_url=handshake_url,
            base_http=code,
        ), 400
    schema_ok = isinstance(hs_resp, dict) and any(
        key in hs_resp for key in ('ok', 'success', 'message', 'data', 'version')
    )
    if hs_code >= 400 or not schema_ok:
        return jsonify(
            ok=False,
            error='panel_handshake_invalid',
            detail='Panel link handshake endpoint missing or unexpected response',
            base_url=base,
            base_http=code,
            handshake_url=handshake_url,
            handshake_http=hs_code,
            handshake_response=hs_resp,
        ), 400

    cfg = ensure_config()
    cfg['admin_base_url'] = base
    if isinstance(hs_resp, dict):
        register_path = str(hs_resp.get('registerPath') or '').strip()
        if register_path:
            cfg['panel_register_path'] = register_path
    cfg['updated_at'] = utc_now()
    ok_write, write_err = write_json(CONFIG_PATH, cfg, mode=0o600)
    if not ok_write:
        return jsonify(ok=False, error=f'Could not persist admin_base_url: {write_err}', base_url=base), 500

    dev = ensure_device()
    fp = ensure_fingerprint()
    host = get_hostname()
    ip = get_ip()

    existing_device = {
        'checked': False,
        'found': False,
        'http': None,
        'code': '',
        'message': '',
        'device_slug': '',
    }
    ping_url = _panel_url(cfg, 'panel_ping_path')
    if ping_url and (ping_url.startswith('http://') or ping_url.startswith('https://')):
        ping_payload = _panel_ping_payload(dev, fp, host, ip)
        p_code, p_resp, _p_err = http_post_json(ping_url, ping_payload, timeout=8)
        existing_device['checked'] = True
        existing_device['http'] = p_code
        if p_code is not None and 200 <= p_code < 300:
            existing_device['found'] = True
            existing_device['code'] = 'device_found'
            existing_device['message'] = 'Gerät ist im Panel bereits bekannt.'
            if isinstance(p_resp, dict):
                p_data = p_resp.get('data') if isinstance(p_resp.get('data'), dict) else {}
                existing_device['device_slug'] = str(
                    p_resp.get('deviceSlug')
                    or p_data.get('deviceSlug')
                    or p_resp.get('slug')
                    or p_data.get('slug')
                ).strip()
        elif isinstance(p_resp, dict):
            err_code = str(p_resp.get('error_code') or p_resp.get('error') or '').strip().lower()
            if err_code in ('device_not_found', 'not_found'):
                existing_device['code'] = 'device_not_found'
                existing_device['message'] = 'Gerät ist im Panel noch nicht registriert.'
            else:
                existing_device['code'] = err_code or 'device_check_unknown'
                existing_device['message'] = str(p_resp.get('message') or p_resp.get('detail') or '').strip()

    update_state(cfg, dev, fp, mode='setup', message='panel URL updated')
    return jsonify(
        ok=True,
        base_url=base,
        http=code,
        handshake_url=handshake_url,
        handshake_http=hs_code,
        handshake_response=hs_resp,
        existing_device=existing_device,
    )


@bp_panel.post('/api/panel/ping')
def api_panel_ping():
    cfg = ensure_config()
    dev = ensure_device()
    fp = ensure_fingerprint()
    host = get_hostname()
    ip = get_ip()
    data = request.get_json(force=True, silent=True) or {}

    request_base = _safe_base_url((data.get('admin_base_url') or ''))
    if request_base:
        cfg['admin_base_url'] = request_base
        cfg['updated_at'] = utc_now()
        write_json(CONFIG_PATH, cfg, mode=0o600)

    url = _panel_url(cfg, 'panel_ping_path')
    if not url:
        return jsonify(
            ok=False,
            error='admin_base_url missing',
            admin_base_url=_safe_base_url(cfg.get('admin_base_url', '')),
            panel_ping_path=cfg.get('panel_ping_path'),
        ), 400
    if not (url.startswith('http://') or url.startswith('https://')):
        return jsonify(
            ok=False,
            error='invalid_panel_url',
            resolved_url=url,
            admin_base_url=_safe_base_url(cfg.get('admin_base_url', '')),
            panel_ping_path=cfg.get('panel_ping_path'),
        ), 400

    payload = _panel_ping_payload(dev, fp, host, ip)
    code, resp, err = http_post_json(url, payload, timeout=8)

    if code is None:
        st = _update_panel_link_state(
            cfg,
            linked=_sticky_linked(cfg, False, None, None),
            last_http=None,
            last_response=None,
            last_error=str(err),
        )
        fail_mode = 'play' if st.get('linked') and cfg.get('selected_stream_slug') else 'setup'
        update_state(cfg, dev, fp, mode=fail_mode, message='panel ping failed', panel_state_overrides=st)
        return jsonify(ok=False, error=str(err), panel_link_state=st), 502

    linked = _sticky_linked(cfg, _response_indicates_success(code, resp), code, resp)
    flags = _extract_panel_device_flags(resp if isinstance(resp, dict) else None)
    if isinstance(flags, dict):
        cfg['panel_device_flags'] = flags
    panel_error = '' if linked else (resp.get('error') if isinstance(resp, dict) else '') or f'http {code}'

    if isinstance(resp, dict):
        slug = (resp.get('deviceSlug') or resp.get('slug') or '').strip()
        if slug:
            cfg['device_slug'] = slug
            cfg['updated_at'] = utc_now()
            write_json(CONFIG_PATH, cfg, mode=0o600)

    st = _update_panel_link_state(cfg, linked=linked, last_http=code, last_response=resp, last_error=panel_error)
    mode = 'play' if st.get('linked') and cfg.get('selected_stream_slug') else 'setup'
    update_state(cfg, dev, fp, mode=mode, message='panel ping', panel_state_overrides=st)

    return jsonify(
        ok=(code == 200),
        panel_link_state=st,
        panel_device_flags=(cfg.get('panel_device_flags') if isinstance(cfg.get('panel_device_flags'), dict) else {}),
        response=resp,
        http=code,
        resolved_url=url,
    )


@bp_panel.post('/api/panel/register')
def api_panel_register():
    cfg = ensure_config()
    dev = ensure_device()
    fp = collect_fingerprint()
    host = get_hostname()
    ip = get_ip()

    data = request.get_json(force=True, silent=True) or {}
    request_base = _safe_base_url((data.get('admin_base_url') or ''))
    if request_base:
        cfg['admin_base_url'] = request_base
        cfg['updated_at'] = utc_now()
        write_json(CONFIG_PATH, cfg, mode=0o600)

    token = (data.get('registration_token') or cfg.get('registration_token') or '').strip()
    if not token:
        return jsonify(ok=False, error='registration_token missing'), 400

    url = _panel_url(cfg, 'panel_register_path')
    if not url:
        return jsonify(
            ok=False,
            error='admin_base_url missing',
            admin_base_url=_safe_base_url(cfg.get('admin_base_url', '')),
            panel_register_path=cfg.get('panel_register_path'),
        ), 400
    if not (url.startswith('http://') or url.startswith('https://')):
        return jsonify(
            ok=False,
            error='invalid_panel_url',
            resolved_url=url,
            admin_base_url=_safe_base_url(cfg.get('admin_base_url', '')),
            panel_register_path=cfg.get('panel_register_path'),
        ), 400

    payload = _panel_register_payload(cfg, dev, fp, host, ip, token)
    # Optional wizard context for panel-side direct assignment hooks.
    assignment = data.get('assignment') if isinstance(data.get('assignment'), dict) else {}
    if assignment:
        payload['assignment'] = assignment
    link_target_type = (data.get('link_target_type') or '').strip()
    link_target_id = str(data.get('link_target_id') or '').strip()
    if link_target_type in ('user', 'customer') and link_target_id:
        payload['linkTargetType'] = link_target_type
        payload['linkTargetId'] = link_target_id
        payload['link_target_type'] = link_target_type
        payload['link_target_id'] = link_target_id
    code, resp, err = http_post_json(url, payload, timeout=8)

    if code is None:
        st = _update_panel_link_state(
            cfg,
            linked=_sticky_linked(cfg, False, None, None),
            last_http=None,
            last_response=None,
            last_error=str(err),
        )
        fail_mode = 'play' if st.get('linked') and cfg.get('selected_stream_slug') else 'setup'
        update_state(cfg, dev, fp, mode=fail_mode, message='register failed', panel_state_overrides=st)
        return jsonify(ok=False, error=str(err), panel_link_state=st), 502

    linked = _sticky_linked(cfg, _response_indicates_success(code, resp), code, resp)
    flags = _extract_panel_device_flags(resp if isinstance(resp, dict) else None)
    if isinstance(flags, dict):
        cfg['panel_device_flags'] = flags
    panel_error = '' if linked else (resp.get('error') if isinstance(resp, dict) else '') or f'http {code}'

    if _response_indicates_success(code, resp):
        cfg['registration_token'] = token
    if isinstance(resp, dict):
        slug = (resp.get('deviceSlug') or resp.get('slug') or '').strip()
        if slug:
            cfg['device_slug'] = slug
    cfg['updated_at'] = utc_now()
    write_json(CONFIG_PATH, cfg, mode=0o600)

    st = _update_panel_link_state(cfg, linked=linked, last_http=code, last_response=resp, last_error=panel_error)
    mode = 'play' if st.get('linked') and cfg.get('selected_stream_slug') else 'setup'
    update_state(cfg, dev, fp, mode=mode, message='panel register', panel_state_overrides=st)

    if _response_indicates_success(code, resp):
        return jsonify(
            ok=True,
            panel_link_state=st,
            panel_device_flags=(cfg.get('panel_device_flags') if isinstance(cfg.get('panel_device_flags'), dict) else {}),
            response=resp,
            http=code,
            resolved_url=url,
        ), 200

    panel_msg = _extract_response_message(resp) or panel_error or 'Registrierung fehlgeschlagen.'
    return jsonify(
        ok=False,
        error='register_failed',
        detail=panel_msg,
        panel_link_state=st,
        panel_device_flags=(cfg.get('panel_device_flags') if isinstance(cfg.get('panel_device_flags'), dict) else {}),
        response=resp,
        http=code,
        resolved_url=url,
    ), 400


@bp_panel.post('/api/panel/sync-status')
def api_panel_sync_status():
    cfg = ensure_config()
    dev = ensure_device()

    data = request.get_json(force=True, silent=True) or {}
    request_base = _safe_base_url((data.get('admin_base_url') or ''))
    base_url = request_base or _safe_base_url(cfg.get('admin_base_url', ''))
    if not base_url:
        return jsonify(ok=False, error='admin_base_url missing'), 400

    sync_url = _panel_sync_status_url(base_url)
    payload = {
        'deviceUuid': dev.get('device_uuid') or '',
        'device_uuid': dev.get('device_uuid') or '',
        'authKey': dev.get('auth_key') or '',
        'auth_key': dev.get('auth_key') or '',
    }

    code, resp, err = http_post_json(sync_url, payload, timeout=8)
    if code is None:
        return jsonify(ok=False, error='sync_status_failed', detail=str(err), sync_url=sync_url), 502
    if code < 200 or code >= 300:
        detail_msg = _extract_response_message(resp) or (resp.get('error') if isinstance(resp, dict) else '') or f'http {code}'
        err_code = str((resp.get('error_code') if isinstance(resp, dict) else '') or (resp.get('error') if isinstance(resp, dict) else '')).strip().lower()
        st = _update_panel_link_state(
            cfg,
            linked=_sticky_linked(cfg, False, code, resp),
            last_http=code,
            last_response=resp,
            last_error=(resp.get('error') if isinstance(resp, dict) else '') or f'http {code}',
        )
        if err_code in ('device_not_found', 'not_found'):
            return jsonify(
                ok=False,
                error='device_not_registered',
                detail=detail_msg or 'Gerät im Panel nicht gefunden. Bitte Setup-Assistent (URL + Token) erneut ausführen.',
                hint='setup_required',
                http=code,
                sync_url=sync_url,
                response=resp,
                panel_link_state=st,
            ), 400
        if err_code in ('auth_invalid', 'invalid_auth'):
            return jsonify(
                ok=False,
                error='device_auth_invalid',
                detail=detail_msg or 'Device-Credentials im Panel nicht gültig.',
                hint='relink_required',
                http=code,
                sync_url=sync_url,
                response=resp,
                panel_link_state=st,
            ), 400
        return jsonify(
            ok=False,
            error='sync_status_failed',
            detail=detail_msg,
            http=code,
            sync_url=sync_url,
            response=resp,
            panel_link_state=st,
        ), 400

    ping_http = None
    ping_error = ''
    ping_url = _panel_url(cfg, 'panel_ping_path')
    if ping_url and (ping_url.startswith('http://') or ping_url.startswith('https://')):
        fp = ensure_fingerprint()
        host = get_hostname()
        ip = get_ip()
        ping_payload = _panel_ping_payload(dev, fp, host, ip)
        ping_http, ping_resp, ping_err = http_post_json(ping_url, ping_payload, timeout=8)
        if ping_http is not None and 200 <= ping_http < 300:
            flags = _extract_panel_device_flags(ping_resp if isinstance(ping_resp, dict) else None)
            if isinstance(flags, dict):
                cfg['panel_device_flags'] = flags
        elif ping_http is None:
            ping_error = str(ping_err or '')

    _update_panel_link_state(
        cfg,
        linked=_sticky_linked(cfg, _response_indicates_success(code, resp), code, resp),
        last_http=code,
        last_response=resp,
        last_error='',
    )

    if request_base:
        cfg['admin_base_url'] = request_base
        cfg['updated_at'] = utc_now()
        write_json(CONFIG_PATH, cfg, mode=0o600)

    return jsonify(
        ok=True,
        http=code,
        sync_url=sync_url,
        response=resp,
        panel_device_flags=(cfg.get('panel_device_flags') if isinstance(cfg.get('panel_device_flags'), dict) else {}),
        ping_http=ping_http,
        ping_error=ping_error,
    ), 200


@bp_panel.post('/api/panel/sync-now')
def api_panel_sync_now():
    cfg = ensure_config()
    dev = ensure_device()
    fp = collect_fingerprint()
    host = get_hostname()
    ip = get_ip()

    data = request.get_json(force=True, silent=True) or {}
    request_base = _safe_base_url((data.get('admin_base_url') or ''))
    if request_base:
        cfg['admin_base_url'] = request_base
        cfg['updated_at'] = utc_now()
        write_json(CONFIG_PATH, cfg, mode=0o600)

    url = _panel_url(cfg, 'panel_register_path')
    if not url:
        return jsonify(
            ok=False,
            error='admin_base_url missing',
            admin_base_url=_safe_base_url(cfg.get('admin_base_url', '')),
            panel_register_path=cfg.get('panel_register_path'),
        ), 400
    if not (url.startswith('http://') or url.startswith('https://')):
        return jsonify(
            ok=False,
            error='invalid_panel_url',
            resolved_url=url,
            admin_base_url=_safe_base_url(cfg.get('admin_base_url', '')),
            panel_register_path=cfg.get('panel_register_path'),
        ), 400

    # First try ping without token. If device already exists in panel, this is enough
    # and avoids unnecessary token errors.
    ping_url = _panel_url(cfg, 'panel_ping_path')
    ping_code = None
    ping_resp = None
    if ping_url and (ping_url.startswith('http://') or ping_url.startswith('https://')):
        ping_payload = _panel_ping_payload(dev, fp, host, ip)
        ping_code, ping_resp, _ping_err = http_post_json(ping_url, ping_payload, timeout=8)
        if ping_code is not None and 200 <= ping_code < 300:
            flags = _extract_panel_device_flags(ping_resp if isinstance(ping_resp, dict) else None)
            if isinstance(flags, dict):
                cfg['panel_device_flags'] = flags
                cfg['updated_at'] = utc_now()
                write_json(CONFIG_PATH, cfg, mode=0o600)
            _update_panel_link_state(cfg, linked=True, last_http=ping_code, last_response=ping_resp, last_error='')
            return jsonify(
                ok=True,
                synced=True,
                mode='ping',
                http=ping_code,
                panel_ping_url=ping_url,
                response=ping_resp,
                panel_device_flags=(cfg.get('panel_device_flags') if isinstance(cfg.get('panel_device_flags'), dict) else {}),
            ), 200

    payload = _panel_sync_payload(cfg, dev, fp, host, ip)
    code, resp, err = http_post_json(url, payload, timeout=10)

    if code is None:
        return jsonify(ok=False, error='sync_now_failed', detail=str(err), panel_register_url=url), 502

    success = _response_indicates_success(code, resp)
    if not success:
        _update_panel_link_state(
            cfg,
            linked=_sticky_linked(cfg, False, code, resp),
            last_http=code,
            last_response=resp,
            last_error=(resp.get('error') if isinstance(resp, dict) else '') or f'http {code}',
        )
        panel_msg = _extract_response_message(resp) or f'http {code}'
        err_code = str((resp.get('error_code') if isinstance(resp, dict) else '') or (resp.get('error') if isinstance(resp, dict) else '')).strip().lower()
        if err_code in ('token_missing', 'token_invalid'):
            return jsonify(
                ok=False,
                error='setup_token_required',
                detail='Gerät ist im Panel noch nicht registriert. Bitte Setup-Assistent mit Token ausführen.',
                hint='open_setup_wizard',
                http=code,
                panel_register_url=url,
                panel_ping_http=ping_code,
                panel_ping_response=ping_resp,
                response=resp,
                panel_device_flags=(cfg.get('panel_device_flags') if isinstance(cfg.get('panel_device_flags'), dict) else {}),
            ), 400
        return jsonify(
            ok=False,
            error='sync_now_failed',
            detail=panel_msg,
            http=code,
            panel_register_url=url,
            response=resp,
            panel_ping_http=ping_code,
            panel_ping_response=ping_resp,
            panel_device_flags=(cfg.get('panel_device_flags') if isinstance(cfg.get('panel_device_flags'), dict) else {}),
        ), 400

    if isinstance(resp, dict):
        slug = (resp.get('deviceSlug') or resp.get('slug') or '').strip()
        if slug:
            cfg['device_slug'] = slug
            cfg['updated_at'] = utc_now()
            write_json(CONFIG_PATH, cfg, mode=0o600)
        flags = _extract_panel_device_flags(resp)
        if isinstance(flags, dict):
            cfg['panel_device_flags'] = flags
            cfg['updated_at'] = utc_now()
            write_json(CONFIG_PATH, cfg, mode=0o600)

    _update_panel_link_state(cfg, linked=True, last_http=code, last_response=resp, last_error='')

    return jsonify(
        ok=True,
        synced=True,
        mode='register',
        http=code,
        panel_register_url=url,
        response=resp,
        panel_ping_http=ping_code,
        panel_ping_response=ping_resp,
        panel_device_flags=(cfg.get('panel_device_flags') if isinstance(cfg.get('panel_device_flags'), dict) else {}),
    ), 200


@bp_panel.post('/api/panel/admin-sync-payload')
def api_panel_admin_sync_payload():
    cfg = ensure_config()
    dev = ensure_device()
    data = request.get_json(force=True, silent=True) or {}

    auth_ok, auth_error = _portal_auth_valid(data, dev)
    if not auth_ok:
        return jsonify(ok=False, error=auth_error), 401

    refresh_fingerprint = _is_truthy(data.get('refresh_fingerprint', True))
    fp = collect_fingerprint() if refresh_fingerprint else ensure_fingerprint()
    host = get_hostname()
    ip = get_ip()

    payload = _panel_sync_payload(cfg, dev, fp, host, ip)
    st = cfg.get('panel_link_state') if isinstance(cfg.get('panel_link_state'), dict) else {}

    mode = 'play' if st.get('linked') and cfg.get('selected_stream_slug') else 'setup'
    update_state(cfg, dev, fp, mode=mode, message='admin live payload', panel_state_overrides=st)

    return jsonify(
        ok=True,
        payload=payload,
        panel_link_state=st,
        collected_at=utc_now(),
    ), 200


@bp_panel.post('/api/panel/rebuild-fingerprint')
def api_panel_rebuild_fingerprint():
    cfg = ensure_config()
    dev = ensure_device()
    host = get_hostname()
    ip = get_ip()

    fp = collect_fingerprint()
    update_state(
        cfg,
        dev,
        fp,
        mode='play' if cfg.get('selected_stream_slug') else 'setup',
        message='fingerprint rebuilt',
    )

    st = cfg.get('panel_link_state') if isinstance(cfg.get('panel_link_state'), dict) else {}
    linked = bool(st.get('linked'))
    base = _safe_base_url(cfg.get('admin_base_url', ''))
    if not linked or not base:
        return jsonify(
            ok=True,
            rebuilt=True,
            synced=False,
            reason='not_linked',
            fingerprint=fp,
            panel_link_state=st,
        ), 200

    url = _panel_url(cfg, 'panel_register_path')
    if not url or not (url.startswith('http://') or url.startswith('https://')):
        return jsonify(
            ok=True,
            rebuilt=True,
            synced=False,
            reason='invalid_panel_url',
            resolved_url=url or '',
            fingerprint=fp,
            panel_link_state=st,
        ), 200

    payload = _panel_sync_payload(cfg, dev, fp, host, ip)
    code, resp, err = http_post_json(url, payload, timeout=10)
    if code is None:
        st = _update_panel_link_state(cfg, linked=_sticky_linked(cfg, False, None, None), last_http=None, last_response=None, last_error=str(err))
        return jsonify(
            ok=False,
            rebuilt=True,
            synced=False,
            error='sync_failed',
            detail=str(err),
            fingerprint=fp,
            panel_link_state=st,
        ), 502

    success = _response_indicates_success(code, resp)
    st = _update_panel_link_state(
        cfg,
        linked=_sticky_linked(cfg, success, code, resp),
        last_http=code,
        last_response=resp,
        last_error='' if success else ((resp.get('error') if isinstance(resp, dict) else '') or f'http {code}'),
    )
    if not success:
        panel_msg = _extract_response_message(resp) or f'http {code}'
        return jsonify(
            ok=False,
            rebuilt=True,
            synced=False,
            error='sync_failed',
            detail=panel_msg,
            http=code,
            response=resp,
            fingerprint=fp,
            panel_link_state=st,
        ), 400

    return jsonify(
        ok=True,
        rebuilt=True,
        synced=True,
        http=code,
        response=resp,
        fingerprint=fp,
        panel_link_state=st,
    ), 200


@bp_panel.post('/api/panel/validate-token')
def api_panel_validate_token():
    cfg = ensure_config()
    dev = ensure_device()

    data = request.get_json(force=True, silent=True) or {}
    request_base = _safe_base_url((data.get('admin_base_url') or ''))
    base_url = request_base or _safe_base_url(cfg.get('admin_base_url', ''))
    if not base_url:
        return jsonify(ok=False, error='admin_base_url missing'), 400

    token = (data.get('registration_token') or '').strip()
    if not token:
        return jsonify(ok=False, error='registration_token missing'), 400

    verify_url = _panel_verify_token_url(base_url)
    payload = {
        'registrationToken': token,
        'token': token,
        'deviceUuid': dev.get('device_uuid') or '',
        'device_uuid': dev.get('device_uuid') or '',
    }
    code, resp, err = http_post_json(verify_url, payload, timeout=8)
    if code is None:
        return jsonify(ok=False, error='token_verify_failed', detail=str(err), verify_url=verify_url), 502
    if code in (404, 405):
        # Backward-compatible fallback: older panels may not expose a dedicated verify endpoint.
        return jsonify(
            ok=True,
            valid=True,
            skipped=True,
            message='Token-Prüfung wird beim Registrieren durchgeführt.',
            http=code,
            verify_url=verify_url,
            response=resp,
        ), 200

    valid = _response_indicates_success(code, resp)
    if request_base and valid:
        cfg['admin_base_url'] = request_base
        cfg['updated_at'] = utc_now()
        write_json(CONFIG_PATH, cfg, mode=0o600)
    if valid:
        return jsonify(ok=True, valid=True, http=code, verify_url=verify_url, response=resp), 200
    panel_msg = _extract_response_message(resp) or 'Token ungültig oder abgelaufen.'
    return jsonify(
        ok=False,
        error='token_invalid',
        detail=panel_msg,
        valid=False,
        http=code,
        verify_url=verify_url,
        response=resp,
    ), 400


def _search_proxy(target: str):
    cfg = ensure_config()
    dev = ensure_device()

    q = (request.args.get('q') or '').strip()
    token = (request.args.get('registration_token') or request.args.get('token') or '').strip()
    request_base = _safe_base_url((request.args.get('admin_base_url') or ''))
    base_url = request_base or _safe_base_url(cfg.get('admin_base_url', ''))
    if not base_url:
        return jsonify(ok=False, error='admin_base_url missing'), 400
    if len(q) < 2:
        return jsonify(ok=False, error='query_too_short', detail='Search query must be at least 2 chars'), 400
    search_path = 'users' if target == 'users' else 'customers'
    search_url = f"{base_url}/api/device/link/search/{search_path}"
    payload = {
        'q': q,
        'query': q,
        'deviceUuid': dev.get('device_uuid') or '',
        'authKey': dev.get('auth_key') or '',
    }
    if token:
        payload['registrationToken'] = token
        payload['token'] = token
    code, resp, err = http_post_json(search_url, payload, timeout=8)
    if code is not None and code >= 400:
        # Backward compatible fallback if panel endpoint expects GET query params.
        query_payload = {
            'q': q,
            'deviceUuid': dev.get('device_uuid') or '',
            'authKey': dev.get('auth_key') or '',
        }
        if token:
            query_payload['token'] = token
            query_payload['registrationToken'] = token
        fallback_url = f"{search_url}?{urlencode(query_payload)}"
        get_code, get_resp, get_err = http_get_json(fallback_url, timeout=8)
        if get_code is not None and 200 <= get_code < 300:
            code, resp, err = get_code, get_resp, ''
        elif get_code is None and err == '':
            err = get_err
    if code is None:
        return jsonify(ok=False, error='search_failed', detail=str(err), search_url=search_url), 502
    if code < 200 or code >= 300:
        return jsonify(ok=False, error='search_failed', http=code, search_url=search_url, response=resp), 400

    items = _normalize_search_items(_extract_items(resp))
    if request_base:
        cfg['admin_base_url'] = request_base
        cfg['updated_at'] = utc_now()
        write_json(CONFIG_PATH, cfg, mode=0o600)
    return jsonify(ok=True, items=items, http=code, response=resp, search_url=search_url)


@bp_panel.get('/api/panel/search-users')
def api_panel_search_users():
    return _search_proxy('users')


@bp_panel.get('/api/panel/search-customers')
def api_panel_search_customers():
    return _search_proxy('customers')


@bp_panel.post('/api/panel/assign')
def api_panel_assign():
    cfg = ensure_config()
    dev = ensure_device()

    data = request.get_json(force=True, silent=True) or {}
    request_base = _safe_base_url((data.get('admin_base_url') or ''))
    base_url = request_base or _safe_base_url(cfg.get('admin_base_url', ''))
    if not base_url:
        return jsonify(ok=False, error='admin_base_url missing'), 400

    token = (data.get('registration_token') or data.get('token') or '').strip()

    target_type = (data.get('target_type') or data.get('link_target_type') or '').strip().lower()
    target_id = str(data.get('target_id') or data.get('link_target_id') or '').strip()
    if target_type not in ('user', 'customer'):
        return jsonify(ok=False, error='target_type invalid'), 400
    if not target_id:
        return jsonify(ok=False, error='target_id missing'), 400

    assign_url = _panel_assign_url(base_url)
    payload = {
        'targetType': target_type,
        'targetId': target_id,
        'target_type': target_type,
        'target_id': target_id,
        'deviceUuid': dev.get('device_uuid') or '',
        'device_uuid': dev.get('device_uuid') or '',
        'authKey': dev.get('auth_key') or '',
        'auth_key': dev.get('auth_key') or '',
    }
    if token:
        payload['registrationToken'] = token
        payload['token'] = token
    code, resp, err = http_post_json(assign_url, payload, timeout=8)
    if code is None:
        return jsonify(ok=False, error='assign_failed', detail=str(err), assign_url=assign_url), 502
    assigned = _response_indicates_success(code, resp)
    if not assigned:
        return jsonify(ok=False, error='assign_failed', http=code, assign_url=assign_url, response=resp), 400

    if request_base:
        cfg['admin_base_url'] = request_base
    cfg['updated_at'] = utc_now()
    write_json(CONFIG_PATH, cfg, mode=0o600)
    return jsonify(ok=True, assigned=True, http=code, assign_url=assign_url, response=resp)


@bp_panel.get('/api/panel/link-status')
def api_panel_link_status():
    cfg = ensure_config()
    st = cfg.get('panel_link_state') if isinstance(cfg.get('panel_link_state'), dict) else {}
    return jsonify(
        ok=True,
        linked=bool(st.get('linked')),
        admin_base_url=_safe_base_url(cfg.get('admin_base_url', '')),
        panel_register_path=cfg.get('panel_register_path'),
        panel_ping_path=cfg.get('panel_ping_path'),
        panel_link_state=st,
        panel_device_flags=(cfg.get('panel_device_flags') if isinstance(cfg.get('panel_device_flags'), dict) else {}),
        device_slug=cfg.get('device_slug') or '',
    )


@bp_panel.post('/api/panel/unlink')
def api_panel_unlink():
    cfg = ensure_config()
    dev = ensure_device()
    fp = ensure_fingerprint()

    cfg['registration_token'] = ''
    cfg['panel_link_state'] = {
        'linked': False,
        'last_check': utc_now(),
        'last_http': None,
        'last_response': None,
        'last_error': 'unlinked',
    }
    cfg['panel_device_flags'] = {
        'is_active': None,
        'is_locked': None,
        'updated_at': utc_now(),
    }
    cfg['updated_at'] = utc_now()
    write_json(CONFIG_PATH, cfg, mode=0o600)

    update_state(cfg, dev, fp, mode='setup', message='panel unlinked', panel_state_overrides=cfg['panel_link_state'])
    return jsonify(ok=True, panel_link_state=cfg['panel_link_state'], panel_device_flags=cfg['panel_device_flags'])
