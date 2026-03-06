from __future__ import annotations

import json
import platform
import shutil
from typing import Any

from app.core.jsonio import read_json, write_json
from app.core.paths import FINGERPRINT_PATH
from app.core.systeminfo import (
    get_hostname,
    parse_mem_total_kb,
    parse_os_release,
    read_machine_id,
    read_pi_serial,
    run_cmd,
)
from app.core.timeutil import utc_now


def _cpu_model() -> str:
    try:
        with open('/proc/cpuinfo', 'r', encoding='utf-8') as f:
            for line in f:
                if line.lower().startswith('model'):
                    return line.split(':', 1)[1].strip()
    except Exception:
        return ''
    return ''


def _json_cmd(bin_name: str, args: list[str], timeout: int = 4) -> Any:
    binary = shutil.which(bin_name)
    if not binary:
        return None
    rc, out, _ = run_cmd([binary] + args, timeout=timeout)
    if rc != 0 or not out:
        return None
    try:
        return json.loads(out)
    except Exception:
        return None


def collect_fingerprint() -> dict:
    osr = parse_os_release()
    fp = {
        'collected_at': utc_now(),
        'hostname': get_hostname(),
        'os': {
            'pretty_name': osr.get('PRETTY_NAME', ''),
            'version': osr.get('VERSION', ''),
            'id': osr.get('ID', ''),
        },
        'kernel': platform.release(),
        'machine': platform.machine(),
        'cpu': {
            'model': _cpu_model(),
            'serial': read_pi_serial(),
        },
        'machine_id': read_machine_id(),
        'memory': {
            'mem_total_kb': parse_mem_total_kb(),
        },
        'disks': _json_cmd('lsblk', ['-J', '-o', 'NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE']),
        'network': _json_cmd('ip', ['-j', 'addr']),
    }
    write_json(FINGERPRINT_PATH, fp, mode=0o600)
    return fp


def ensure_fingerprint() -> dict:
    fp = read_json(FINGERPRINT_PATH, None)
    if isinstance(fp, dict) and fp:
        return fp
    return collect_fingerprint()


def short_fingerprint(fp: dict) -> dict:
    return {
        'hostname': fp.get('hostname', ''),
        'os': fp.get('os', {}),
        'kernel': fp.get('kernel', ''),
        'machine': fp.get('machine', ''),
        'cpu_model': ((fp.get('cpu') or {}).get('model') if isinstance(fp.get('cpu'), dict) else ''),
        'memory': fp.get('memory', {}) if isinstance(fp.get('memory'), dict) else {},
        'collected_at': fp.get('collected_at'),
    }
