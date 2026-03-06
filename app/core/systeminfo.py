from __future__ import annotations

import os
import shutil
import socket
import subprocess
import math
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


def parse_mem_stats_kb() -> dict[str, int | None]:
    stats: dict[str, int | None] = {
        "mem_total_kb": None,
        "mem_free_kb": None,
        "mem_available_kb": None,
    }
    try:
        with open('/proc/meminfo', 'r', encoding='utf-8') as f:
            for line in f:
                if ':' not in line:
                    continue
                key, rest = line.split(':', 1)
                parts = rest.strip().split()
                if not parts:
                    continue
                try:
                    value = int(parts[0])
                except Exception:
                    continue
                if key == 'MemTotal':
                    stats["mem_total_kb"] = value
                elif key == 'MemFree':
                    stats["mem_free_kb"] = value
                elif key == 'MemAvailable':
                    stats["mem_available_kb"] = value
    except Exception:
        return stats
    return stats


def parse_load_stats() -> dict[str, float | int | None]:
    stats: dict[str, float | int | None] = {
        "load_1m": None,
        "load_5m": None,
        "load_15m": None,
        "cpu_cores": None,
        "cpu_percent_estimate": None,
    }
    try:
        load1, load5, load15 = os.getloadavg()
        cores = os.cpu_count() or 1
        percent = max(0.0, min(100.0, (load1 / float(cores)) * 100.0))
        stats["load_1m"] = float(load1)
        stats["load_5m"] = float(load5)
        stats["load_15m"] = float(load15)
        stats["cpu_cores"] = int(cores)
        stats["cpu_percent_estimate"] = float(math.floor(percent * 10.0) / 10.0)
    except Exception:
        return stats
    return stats
