from __future__ import annotations

import urllib.parse

from flask import Blueprint, jsonify, request

from app.core.config import _safe_base_url, ensure_config
from app.core.device import ensure_device
from app.core.fingerprint import ensure_fingerprint
from app.core.httpclient import http_get_json
from app.core.jsonio import read_json, write_json
from app.core.paths import PLAN_PATH
from app.core.state import update_state
from app.core.timeutil import utc_now

bp_plan = Blueprint('plan', __name__)


@bp_plan.post('/api/plan/pull')
def api_plan_pull():
    cfg = ensure_config()
    dev = ensure_device()
    fp = ensure_fingerprint()
    data = request.get_json(force=True, silent=True) or {}

    base = _safe_base_url(data.get('admin_base_url') or cfg.get('admin_base_url') or '')
    device_slug = (data.get('deviceSlug') or cfg.get('device_slug') or '').strip()
    stream_slug = (data.get('streamSlug') or cfg.get('selected_stream_slug') or '').strip()

    if not base:
        return jsonify(ok=False, error='missing_admin_base_url'), 400
    if not device_slug:
        return jsonify(ok=False, error='missing_device_slug'), 400
    if not stream_slug:
        return jsonify(ok=False, error='missing_stream_slug'), 400

    url = (
        f"{base}/api/raspi/device/{urllib.parse.quote(device_slug)}/"
        f"playback-plan/{urllib.parse.quote(stream_slug)}"
    )
    code, payload, err = http_get_json(url, timeout=10)
    if code is None:
        return jsonify(ok=False, error=str(err)), 502
    if code != 200:
        return jsonify(ok=False, error=f'panel_http_{code}', panel_response=payload), code
    if not isinstance(payload, dict):
        return jsonify(ok=False, error='panel_invalid_response'), 502

    raw = payload.get('raw') if isinstance(payload.get('raw'), str) else ''
    if raw and ('<html' in raw.lower() or '<!doctype' in raw.lower()):
        return jsonify(ok=False, error='panel_non_json'), 502

    plan_wrapper = {
        'version': 1,
        'saved_at': utc_now(),
        'admin_base_url': base,
        'device_slug': device_slug,
        'stream_slug': stream_slug,
        'plan': payload,
    }
    ok, write_err = write_json(PLAN_PATH, plan_wrapper, mode=0o600)
    if not ok:
        return jsonify(ok=False, error=f'write_plan_failed: {write_err}'), 500

    cfg['selected_stream_slug'] = stream_slug
    cfg['selected_stream_updated_at'] = utc_now()
    state, _ = update_state(cfg, dev, fp, mode='play', message='plan updated')
    return jsonify(ok=True, plan=plan_wrapper, state=state)


@bp_plan.get('/api/plan/current')
def api_plan_current():
    data = read_json(PLAN_PATH, None)
    if not isinstance(data, dict) or not data:
        return jsonify(ok=False, error='plan_missing', path=PLAN_PATH), 404
    return jsonify(ok=True, plan=data)
