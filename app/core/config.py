from __future__ import annotations

import secrets

from app.core.jsonio import read_json, write_json
from app.core.paths import CONFIG_PATH
from app.core.timeutil import utc_now

DEFAULT_CONFIG: dict = {
    'admin_base_url': '',
    'poll_seconds': 60,
    'registration_token': '',
    'panel_register_path': '/api/device/link/register',
    'panel_ping_path': '/api/device/ping',
    'selected_stream_slug': '',
    'selected_stream_name': '',
    'selected_stream_updated_at': '',
    'device_slug': '',
    'panel_link_state': {
        'linked': False,
        'last_check': None,
        'last_http': None,
        'last_response': None,
        'last_error': '',
    },
    'panel_device_flags': {
        'is_active': None,
        'is_locked': None,
        'updated_at': None,
    },
    'wifi_profiles': [],
    'preferred_wifi': '',
    'last_wifi_ssid': '',
    'storage_delete_hardcore_mode': False,
    'display_config': {
        'connectors': {},
        'updated_at': None,
    },
    'session_secret': '',
    'panel_linked_users': [],
    'panel_linked_customers': [],
    'panel_api_keys': {
        'raspi_to_admin': '',
        'admin_to_raspi': '',
        'updated_at': None,
    },
    'panel_api_key_bootstrap': {
        'mode': 'none',
        'status': 'none',
        'last_pull_at': None,
        'last_ack_at': None,
        'last_error': '',
    },
}


def clamp_poll_seconds(value: int | str) -> int:
    try:
        value = int(value)
    except Exception:
        value = 60
    return max(15, value)


def _safe_base_url(url: str) -> str:
    url = (url or '').strip()
    if not url:
        return ''
    if not url.startswith('http://') and not url.startswith('https://'):
        url = f'https://{url}'
    return url.rstrip('/')


def _panel_url(cfg: dict, key: str) -> str:
    base = _safe_base_url(cfg.get('admin_base_url', ''))
    path = (cfg.get(key) or '').strip()
    if not path:
        return base
    if path.startswith('http://') or path.startswith('https://'):
        return path
    if not base:
        return ''
    if not path.startswith('/'):
        path = f'/{path}'
    return f'{base}{path}'


def ensure_config() -> dict:
    cfg = read_json(CONFIG_PATH, None)
    if not isinstance(cfg, dict) or not cfg:
        cfg = dict(DEFAULT_CONFIG)
        cfg['created_at'] = utc_now()
        cfg['updated_at'] = utc_now()
        write_json(CONFIG_PATH, cfg, mode=0o600)
        return cfg

    changed = False
    for key, value in DEFAULT_CONFIG.items():
        if key not in cfg:
            cfg[key] = value
            changed = True

    if not isinstance(cfg.get('panel_link_state'), dict):
        cfg['panel_link_state'] = dict(DEFAULT_CONFIG['panel_link_state'])
        changed = True
    else:
        for key, value in DEFAULT_CONFIG['panel_link_state'].items():
            if key not in cfg['panel_link_state']:
                cfg['panel_link_state'][key] = value
                changed = True

    if not isinstance(cfg.get('panel_device_flags'), dict):
        cfg['panel_device_flags'] = dict(DEFAULT_CONFIG['panel_device_flags'])
        changed = True
    else:
        for key, value in DEFAULT_CONFIG['panel_device_flags'].items():
            if key not in cfg['panel_device_flags']:
                cfg['panel_device_flags'][key] = value
                changed = True

    if not isinstance(cfg.get('display_config'), dict):
        cfg['display_config'] = dict(DEFAULT_CONFIG['display_config'])
        changed = True
    else:
        dc = cfg['display_config']
        if not isinstance(dc.get('connectors'), dict):
            dc['connectors'] = {}
            changed = True
        if 'updated_at' not in dc:
            dc['updated_at'] = None
            changed = True

    clamped = clamp_poll_seconds(cfg.get('poll_seconds', 60))
    if clamped != cfg.get('poll_seconds'):
        cfg['poll_seconds'] = clamped
        changed = True

    # Auto-migrate legacy admin endpoint to the new link-wizard register route.
    if (cfg.get('panel_register_path') or '').strip() == '/api/device/register':
        cfg['panel_register_path'] = '/api/device/link/register'
        changed = True

    if 'created_at' not in cfg:
        cfg['created_at'] = utc_now()
        changed = True
    if not isinstance(cfg.get('session_secret'), str):
        cfg['session_secret'] = ''
        changed = True
    if cfg.get('session_secret', '').strip() == '':
        cfg['session_secret'] = secrets.token_urlsafe(48)
        changed = True
    if not isinstance(cfg.get('panel_linked_users'), list):
        cfg['panel_linked_users'] = []
        changed = True
    if not isinstance(cfg.get('panel_linked_customers'), list):
        cfg['panel_linked_customers'] = []
        changed = True
    if not isinstance(cfg.get('panel_api_keys'), dict):
        cfg['panel_api_keys'] = dict(DEFAULT_CONFIG['panel_api_keys'])
        changed = True
    else:
        for key, value in DEFAULT_CONFIG['panel_api_keys'].items():
            if key not in cfg['panel_api_keys']:
                cfg['panel_api_keys'][key] = value
                changed = True
    if not isinstance(cfg.get('panel_api_key_bootstrap'), dict):
        cfg['panel_api_key_bootstrap'] = dict(DEFAULT_CONFIG['panel_api_key_bootstrap'])
        changed = True
    else:
        for key, value in DEFAULT_CONFIG['panel_api_key_bootstrap'].items():
            if key not in cfg['panel_api_key_bootstrap']:
                cfg['panel_api_key_bootstrap'][key] = value
                changed = True
    if changed:
        cfg['updated_at'] = utc_now()
        write_json(CONFIG_PATH, cfg, mode=0o600)
    return cfg
