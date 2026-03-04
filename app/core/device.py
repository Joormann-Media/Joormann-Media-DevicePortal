from __future__ import annotations

import secrets
import uuid

from app.core.jsonio import read_json, write_json
from app.core.paths import DEVICE_PATH
from app.core.systeminfo import read_machine_id, read_pi_serial
from app.core.timeutil import utc_now


def _fallback_pi_serial(dev: dict) -> str:
    machine_id = (dev.get('machine_id') or '').strip()
    device_uuid = (dev.get('device_uuid') or '').strip()
    if machine_id:
        return f'unknown-{machine_id[:8]}'
    if device_uuid:
        return f"unknown-{device_uuid.replace('-', '')[:8]}"
    return 'unknown'


def ensure_device() -> dict:
    dev = read_json(DEVICE_PATH, None)
    if not isinstance(dev, dict) or not dev:
        dev = {}

    changed = False

    if not dev.get('device_uuid'):
        dev['device_uuid'] = str(uuid.uuid4())
        changed = True

    if not dev.get('auth_key'):
        dev['auth_key'] = secrets.token_urlsafe(32)
        changed = True

    pi_serial = read_pi_serial()
    if not dev.get('pi_serial'):
        dev['pi_serial'] = pi_serial or _fallback_pi_serial(dev)
        changed = True
    elif str(dev.get('pi_serial')).startswith('unknown') and pi_serial:
        dev['pi_serial'] = pi_serial
        changed = True

    if not dev.get('machine_id'):
        dev['machine_id'] = read_machine_id()
        changed = True

    if not dev.get('created_at'):
        dev['created_at'] = utc_now()
        changed = True

    if changed:
        write_json(DEVICE_PATH, dev, mode=0o600)

    return dev
