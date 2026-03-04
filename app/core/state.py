from __future__ import annotations

from app.core.jsonio import read_json, write_json
from app.core.paths import STATE_PATH
from app.core.systeminfo import get_hostname, get_ip
from app.core.timeutil import utc_now


def get_state() -> dict:
    state = read_json(STATE_PATH, None)
    if isinstance(state, dict) and state:
        return state
    return {
        'ok': True,
        'mode': 'setup',
        'message': 'initializing',
        'hostname': get_hostname(),
        'ip': get_ip(),
        'panel': {
            'linked': False,
            'last_error': '',
            'last_http': None,
            'last_check': None,
        },
        'selected_stream_slug': '',
        'device_slug': '',
        'updated_at': utc_now(),
    }


def update_state(
    cfg: dict,
    device: dict,
    fingerprint: dict,
    mode: str,
    message: str,
    panel_state_overrides: dict | None = None,
) -> tuple[dict, tuple[bool, str]]:
    prev = get_state()
    panel_cfg = cfg.get('panel_link_state') if isinstance(cfg.get('panel_link_state'), dict) else {}
    panel = {
        'linked': bool(panel_cfg.get('linked')),
        'last_error': panel_cfg.get('last_error') or '',
        'last_http': panel_cfg.get('last_http'),
        'last_check': panel_cfg.get('last_check'),
    }
    if isinstance(panel_state_overrides, dict):
        panel.update(panel_state_overrides)

    state = {
        'ok': True,
        'mode': mode,
        'message': message,
        'hostname': fingerprint.get('hostname') or prev.get('hostname') or get_hostname(),
        'ip': get_ip(),
        'panel': panel,
        'selected_stream_slug': cfg.get('selected_stream_slug') or '',
        'device_slug': cfg.get('device_slug') or '',
        'updated_at': utc_now(),
    }
    ok, err = write_json(STATE_PATH, state, mode=0o600)
    return state, (ok, err)
