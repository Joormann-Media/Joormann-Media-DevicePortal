from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.core.config import _panel_url, _safe_base_url, ensure_config
from app.core.device import ensure_device
from app.core.fingerprint import ensure_fingerprint
from app.core.httpclient import http_get_text, http_post_json
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
    return payload


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

    cfg = ensure_config()
    cfg['admin_base_url'] = base
    cfg['updated_at'] = utc_now()
    write_json(CONFIG_PATH, cfg, mode=0o600)

    dev = ensure_device()
    fp = ensure_fingerprint()
    update_state(cfg, dev, fp, mode='setup', message='panel URL updated')
    return jsonify(ok=True, base_url=base, http=code)


@bp_panel.post('/api/panel/ping')
def api_panel_ping():
    cfg = ensure_config()
    dev = ensure_device()
    fp = ensure_fingerprint()
    host = get_hostname()
    ip = get_ip()

    url = _panel_url(cfg, 'panel_ping_path')
    if not url:
        return jsonify(ok=False, error='admin_base_url missing'), 400

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

    return jsonify(ok=(code == 200), panel_link_state=st, response=resp, http=code)


@bp_panel.post('/api/panel/register')
def api_panel_register():
    cfg = ensure_config()
    dev = ensure_device()
    fp = ensure_fingerprint()
    host = get_hostname()
    ip = get_ip()

    data = request.get_json(force=True, silent=True) or {}
    token = (data.get('registration_token') or cfg.get('registration_token') or '').strip()
    if not token:
        return jsonify(ok=False, error='registration_token missing'), 400

    url = _panel_url(cfg, 'panel_register_path')
    if not url:
        return jsonify(ok=False, error='admin_base_url missing'), 400

    payload = _panel_register_payload(cfg, dev, fp, host, ip, token)
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

    return jsonify(ok=linked, panel_link_state=st, response=resp, http=code), (200 if linked else 400)


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
