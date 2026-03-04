from __future__ import annotations

import os
import shutil
import socket
import subprocess
from typing import Any


def run_cmd(args: list[str], timeout: int = 6) -> tuple[int, str, str]:
    env = os.environ.copy()
    env['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, (p.stdout or '').strip(), (p.stderr or '').strip()
    except Exception as exc:
        return 127, '', str(exc)


def get_hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return 'unknown'


def get_ip() -> str:
    hostname_bin = shutil.which('hostname') or '/bin/hostname'
    rc, out, _ = run_cmd([hostname_bin, '-I'], timeout=3)
    if rc == 0 and out:
        return out.split()[0]
    return '(unknown)'


def read_machine_id() -> str:
    for p in ('/etc/machine-id', '/var/lib/dbus/machine-id'):
        try:
            if os.path.exists(p):
                with open(p, 'r', encoding='utf-8') as f:
                    return f.read().strip()
        except Exception:
            continue
    return ''


def read_pi_serial() -> str:
    try:
        with open('/proc/cpuinfo', 'r', encoding='utf-8') as f:
            for line in f:
                if line.lower().startswith('serial'):
                    return line.split(':', 1)[1].strip()
    except Exception:
        return ''
    return ''


def parse_os_release() -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        with open('/etc/os-release', 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                out[k] = v.strip().strip('"')
    except Exception:
        return {}
    return out


def parse_mem_total_kb() -> int | None:
    try:
        with open('/proc/meminfo', 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith('MemTotal:'):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1])
    except Exception:
        return None
    return None
