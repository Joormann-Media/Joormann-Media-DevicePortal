from __future__ import annotations

import ipaddress

from flask import Blueprint, redirect, render_template, request, url_for

from app.core.auth_mode import resolve_auth_mode
from app.core.auth_session import current_session
from app.core.config import ensure_config
from app.core.connectivity_mode import detect_connectivity_setup_mode
from app.core.netcontrol import NetControlError, get_ap_status
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


def _is_ap_request() -> bool:
    host = (request.host or "").split(":", 1)[0].strip()
    remote_addr = (request.remote_addr or "").strip()

    try:
        ap_status = get_ap_status()
    except NetControlError:
        ap_status = {}

    ap_ip = str(ap_status.get("ip") or "").strip()
    if ap_ip and host == ap_ip:
        return True

    if remote_addr:
        try:
            remote_ip = ipaddress.ip_address(remote_addr)
            if remote_ip in ipaddress.ip_network("192.168.4.0/24"):
                return True
        except ValueError:
            pass

    return False


@bp_ui.get('/')
def index():
    if _is_ap_request():
        return redirect(url_for('ui.wifi_setup'))

    cfg = ensure_config()
    dev = ensure_device()
    fp = ensure_fingerprint()
    state = get_state()
    setup_mode = detect_connectivity_setup_mode()
    auth_mode = resolve_auth_mode(
        cfg,
        force_local=bool(setup_mode.get("active")),
        force_reason="connectivity_setup_mode",
    )
    auth_state = current_session()

    return render_template(
        'index.html',
        hostname=get_hostname(),
        ip=get_ip(),
        cfg=cfg,
        dev={**dev, 'auth_key': _mask_secret(dev.get('auth_key', ''))},
        panel=(cfg.get('panel_link_state') if isinstance(cfg.get('panel_link_state'), dict) else {}),
        fp=short_fingerprint(fp),
            state=state,
            auth_mode=auth_mode,
            connectivity_setup_mode=setup_mode,
            auth_state=auth_state,
        )


@bp_ui.get('/wifi-setup')
def wifi_setup():
    cfg = ensure_config()
    setup_mode = detect_connectivity_setup_mode()
    auth_mode = resolve_auth_mode(
        cfg,
        force_local=True,
        force_reason="ap_wifi_setup_page",
    )
    auth_state = current_session()

    return render_template(
        'wifi_setup.html',
        hostname=get_hostname(),
        ip=get_ip(),
        auth_mode=auth_mode,
        connectivity_setup_mode=setup_mode,
        auth_state=auth_state,
    )
