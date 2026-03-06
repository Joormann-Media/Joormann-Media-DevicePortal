#!/usr/bin/env bash
set -euo pipefail

IFACE="${1:-wlan0}"
IW="$(command -v iw || true)"

if [[ -z "${IW}" ]]; then
  echo '{"clients":[]}'
  exit 0
fi

if [[ ! -d "/sys/class/net/${IFACE}" ]]; then
  echo "interface missing: ${IFACE}" >&2
  exit 4
fi

python3 - "$IFACE" <<'PY'
import json
import subprocess
import sys
from datetime import datetime, timezone

iface = sys.argv[1]

def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()

rc, out, _ = run(["iw", "dev", iface, "station", "dump"])
if rc != 0:
    print(json.dumps({"clients": []}))
    raise SystemExit(0)

stations = []
current = None
for line in out.splitlines():
    line = line.strip()
    if line.startswith("Station "):
        if current:
            stations.append(current)
        mac = line.split()[1].lower()
        current = {"mac": mac}
        continue
    if current is None:
        continue
    if line.startswith("inactive time:"):
        try:
            val = int(line.split(":", 1)[1].strip().split()[0])
        except Exception:
            val = None
        current["inactive_ms"] = val
if current:
    stations.append(current)

rc, neigh_out, _ = run(["ip", "-4", "neigh", "show", "dev", iface])
ip_by_mac = {}
if rc == 0:
    for line in neigh_out.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        ip = parts[0]
        if "lladdr" not in parts:
            continue
        idx = parts.index("lladdr")
        if idx + 1 >= len(parts):
            continue
        mac = parts[idx + 1].lower()
        ip_by_mac[mac] = ip

now = datetime.now(timezone.utc)
clients = []
for sta in stations:
    mac = sta.get("mac", "")
    ip = ip_by_mac.get(mac, "")
    hostname = ""
    if ip:
        rc, host_out, _ = run(["getent", "hosts", ip])
        if rc == 0 and host_out:
            parts = host_out.split()
            if len(parts) >= 2:
                hostname = parts[1]
    inactive_ms = sta.get("inactive_ms")
    connected = True
    last_seen = now.isoformat().replace("+00:00", "Z")
    if isinstance(inactive_ms, int):
        connected = inactive_ms < 30000
    clients.append(
        {
            "mac": mac,
            "ip": ip,
            "hostname": hostname,
            "status": "connected" if connected else "disconnected",
            "last_seen": last_seen,
            "inactive_ms": inactive_ms if isinstance(inactive_ms, int) else None,
        }
    )

print(json.dumps({"clients": clients}, ensure_ascii=False))
PY
