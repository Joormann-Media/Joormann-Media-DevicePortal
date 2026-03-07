from __future__ import annotations

from urllib.parse import urlencode

from flask import Blueprint, jsonify, request

from app.core.config import _panel_url, _safe_base_url, ensure_config
from app.core.device import ensure_device
from app.core.fingerprint import ensure_fingerprint
from app.core.httpclient import http_get_json, http_get_text, http_post_json
from app.core.jsonio import write_json
from app.core.paths import CONFIG_PATH
from app.core.state import update_state
from app.core.systeminfo import get_hostname, get_ip
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
    return payload


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


def _panel_verify_token_url(base_url: str) -> str:
    return f"{base_url}/api/device/link/verify-token"


def _panel_assign_url(base_url: str) -> str:
    return f"{base_url}/api/device/link/assign"


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
    return True


def _extract_response_message(resp: dict | None) -> str:
    if not isinstance(resp, dict):
        return ''
    candidates: list[object] = [
        resp.get('message'),
        resp.get('error'),
        resp.get('detail'),
        (resp.get('data') or {}).get('message') if isinstance(resp.get('data'), dict) else '',
    ]
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
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
    cfg['updated_at'] = utc_now()
    ok_write, write_err = write_json(CONFIG_PATH, cfg, mode=0o600)
    if not ok_write:
        return jsonify(ok=False, error=f'Could not persist admin_base_url: {write_err}', base_url=base), 500

    dev = ensure_device()
    fp = ensure_fingerprint()
    update_state(cfg, dev, fp, mode='setup', message='panel URL updated')
    return jsonify(
        ok=True,
        base_url=base,
        http=code,
        handshake_url=handshake_url,
        handshake_http=hs_code,
        handshake_response=hs_resp,
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
        st = _update_panel_link_state(cfg, linked=False, last_http=None, last_response=None, last_error=str(err))
        update_state(cfg, dev, fp, mode='setup', message='panel ping failed', panel_state_overrides=st)
        return jsonify(ok=False, error=str(err), panel_link_state=st), 502

    linked = code == 200 and isinstance(resp, dict) and bool(resp.get('deviceId') or resp.get('linked'))
    panel_error = '' if linked else (resp.get('error') if isinstance(resp, dict) else '') or f'http {code}'

    if isinstance(resp, dict):
        slug = (resp.get('deviceSlug') or resp.get('slug') or '').strip()
        if slug:
            cfg['device_slug'] = slug
            cfg['updated_at'] = utc_now()
            write_json(CONFIG_PATH, cfg, mode=0o600)

    st = _update_panel_link_state(cfg, linked=linked, last_http=code, last_response=resp, last_error=panel_error)
    mode = 'play' if linked and cfg.get('selected_stream_slug') else 'setup'
    update_state(cfg, dev, fp, mode=mode, message='panel ping', panel_state_overrides=st)

    return jsonify(ok=(code == 200), panel_link_state=st, response=resp, http=code, resolved_url=url)


@bp_panel.post('/api/panel/register')
def api_panel_register():
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
        st = _update_panel_link_state(cfg, linked=False, last_http=None, last_response=None, last_error=str(err))
        update_state(cfg, dev, fp, mode='setup', message='register failed', panel_state_overrides=st)
        return jsonify(ok=False, error=str(err), panel_link_state=st), 502

    linked = code in (200, 201) and isinstance(resp, dict) and bool(resp.get('deviceId') or resp.get('linked'))
    panel_error = '' if linked else (resp.get('error') if isinstance(resp, dict) else '') or f'http {code}'

    if linked:
        cfg['registration_token'] = token
    if isinstance(resp, dict):
        slug = (resp.get('deviceSlug') or resp.get('slug') or '').strip()
        if slug:
            cfg['device_slug'] = slug
    cfg['updated_at'] = utc_now()
    write_json(CONFIG_PATH, cfg, mode=0o600)

    st = _update_panel_link_state(cfg, linked=linked, last_http=code, last_response=resp, last_error=panel_error)
    mode = 'play' if linked and cfg.get('selected_stream_slug') else 'setup'
    update_state(cfg, dev, fp, mode=mode, message='panel register', panel_state_overrides=st)

    if linked:
        return jsonify(ok=True, panel_link_state=st, response=resp, http=code, resolved_url=url), 200

    panel_msg = _extract_response_message(resp) or panel_error or 'Registrierung fehlgeschlagen.'
    return jsonify(
        ok=False,
        error='register_failed',
        detail=panel_msg,
        panel_link_state=st,
        response=resp,
        http=code,
        resolved_url=url,
    ), 400


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
    if not token:
        return jsonify(ok=False, error='registration_token missing'), 400

    search_path = 'users' if target == 'users' else 'customers'
    search_url = f"{base_url}/api/device/link/search/{search_path}"
    payload = {
        'registrationToken': token,
        'token': token,
        'q': q,
        'query': q,
        'deviceUuid': dev.get('device_uuid') or '',
    }
    code, resp, err = http_post_json(search_url, payload, timeout=8)
    if code is not None and code >= 400:
        # Backward compatible fallback if panel endpoint expects GET query params.
        fallback_url = f"{search_url}?{urlencode({'q': q, 'registrationToken': token, 'deviceUuid': dev.get('device_uuid') or ''})}"
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
    if not token:
        return jsonify(ok=False, error='registration_token missing'), 400

    target_type = (data.get('target_type') or data.get('link_target_type') or '').strip().lower()
    target_id = str(data.get('target_id') or data.get('link_target_id') or '').strip()
    if target_type not in ('user', 'customer'):
        return jsonify(ok=False, error='target_type invalid'), 400
    if not target_id:
        return jsonify(ok=False, error='target_id missing'), 400

    assign_url = _panel_assign_url(base_url)
    payload = {
        'registrationToken': token,
        'token': token,
        'targetType': target_type,
        'targetId': target_id,
        'target_type': target_type,
        'target_id': target_id,
        'deviceUuid': dev.get('device_uuid') or '',
        'device_uuid': dev.get('device_uuid') or '',
    }
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
    cfg['updated_at'] = utc_now()
    write_json(CONFIG_PATH, cfg, mode=0o600)

    update_state(cfg, dev, fp, mode='setup', message='panel unlinked', panel_state_overrides=cfg['panel_link_state'])
    return jsonify(ok=True, panel_link_state=cfg['panel_link_state'])
