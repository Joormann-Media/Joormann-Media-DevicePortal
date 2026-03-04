from __future__ import annotations

from flask import Blueprint, render_template

from app.core.config import ensure_config
from app.core.device import ensure_device
from app.core.fingerprint import ensure_fingerprint, short_fingerprint
from app.core.state import get_state
from app.core.systeminfo import get_hostname, get_ip

bp_ui = Blueprint('ui', __name__)


def _mask_secret(value: str, keep: int = 6) -> str:
    value = value or ''
    if len(value) <= keep:
        return '*' * len(value)
    return '********' + value[-keep:]


@bp_ui.get('/')
def index():
    cfg = ensure_config()
    dev = ensure_device()
    fp = ensure_fingerprint()
    state = get_state()

    return render_template(
        'index.html',
        hostname=get_hostname(),
        ip=get_ip(),
        cfg=cfg,
        dev={**dev, 'auth_key': _mask_secret(dev.get('auth_key', ''))},
        panel=(cfg.get('panel_link_state') if isinstance(cfg.get('panel_link_state'), dict) else {}),
        fp=short_fingerprint(fp),
        state=state,
    )
