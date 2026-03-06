from __future__ import annotations

from flask import Blueprint, jsonify

from app.core.config import ensure_config
from app.core.device import ensure_device
from app.core.fingerprint import collect_fingerprint, ensure_fingerprint, short_fingerprint
from app.core.gitinfo import get_update_info
from app.core.state import get_state, update_state

bp_status = Blueprint('status', __name__)


def _mask_secret(value: str, keep: int = 6) -> str:
    value = value or ''
    if len(value) <= keep:
        return '*' * len(value)
    return '********' + value[-keep:]


@bp_status.get('/health')
def health():
    return jsonify(ok=True)


@bp_status.get('/api/status')
def api_status():
    cfg = ensure_config()
    dev = ensure_device()
    fp = ensure_fingerprint()
    mode = 'play' if (cfg.get('selected_stream_slug') or '').strip() else 'setup'
    state, _ = update_state(cfg, dev, fp, mode=mode, message='status')

    dev_view = dict(dev)
    dev_view['auth_key'] = _mask_secret(dev_view.get('auth_key', ''))

    return jsonify(
        ok=True,
        config=cfg,
        device=dev_view,
        fingerprint=short_fingerprint(fp),
        app_update=get_update_info(),
        state=state,
    )


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
