#!/usr/bin/env bash
set -u
# ---------------------------------------------------------------------------
# autodiscover.sh – Deviceportal ist das Portal selbst (Port 5070).
# Dieses Script registriert das Portal als Master-Service bei sich selbst,
# sobald es gestartet ist, damit es im eigenen Dashboard erscheint.
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_DIR="$PROJECT_ROOT/config"
PORTS_ENV_FILE="${JARVIS_PORTS_FILE:-$CONFIG_DIR/ports.env}"
PORTS_LOCAL_FILE="${JARVIS_PORTS_LOCAL_FILE:-$CONFIG_DIR/ports.local.env}"

if [[ -f "$PORTS_ENV_FILE" ]]; then
  set -a; source "$PORTS_ENV_FILE"; set +a
fi
if [[ -f "$PORTS_LOCAL_FILE" ]]; then
  set -a; source "$PORTS_LOCAL_FILE"; set +a
fi

PORTAL_AUTODISCOVER_URL="${PORTAL_AUTODISCOVER_URL:-}"
if [[ -z "$PORTAL_AUTODISCOVER_URL" ]]; then
  PORTAL_HOST_SELF="${PORTAL_HOST:-127.0.0.1}"
  PORTAL_PORT_SELF="${PORTAL_PORT:-5070}"
  PORTAL_AUTODISCOVER_URL="http://${PORTAL_HOST_SELF}:${PORTAL_PORT_SELF}/autodiscover"
fi

if ! command -v curl >/dev/null 2>&1; then
  exit 0
fi

repo_name="$(basename "$PROJECT_ROOT")"
repo_link=""
repo_branch="main"
if command -v git >/dev/null 2>&1; then
  repo_link="$(git -C "$PROJECT_ROOT" remote get-url origin 2>/dev/null || true)"
  branch_guess="$(git -C "$PROJECT_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  if [[ -n "$branch_guess" && "$branch_guess" != "HEAD" ]]; then
    repo_branch="$branch_guess"
  fi
fi
if [[ "$repo_link" =~ ^git@github.com:(.+)$ ]]; then
  repo_link="https://github.com/${BASH_REMATCH[1]}"
fi
if [[ -n "$repo_link" && ! "$repo_link" =~ \.git$ ]]; then
  repo_link="${repo_link}.git"
fi

service_port="${PORTAL_PORT:-5070}"
lan_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
host_ip="${lan_ip:-127.0.0.1}"
api_base_url="http://${host_ip}:${service_port}"

payload="$(python3 - <<PY
import json
print(json.dumps({
  "repo_name": ${repo_name@Q},
  "repo_link": ${repo_link@Q},
  "repo_branch": ${repo_branch@Q},
  "install_dir": ${PROJECT_ROOT@Q},
  "service_name": "device-portal.service",
  "service_id": "jsvc_deviceportal_master",
  "service_user": "${USER:-}",
  "use_service": True,
  "autostart": True,
  "service_port": int("${service_port}") if "${service_port}".isdigit() else None,
  "api_base_url": ${api_base_url@Q},
  "health_url": ${api_base_url@Q} + "/health",
  "ui_url": ${api_base_url@Q} + "/",
  "endpoints": {
    "api_base": ${api_base_url@Q},
    "health": ${api_base_url@Q} + "/health",
    "ui": ${api_base_url@Q} + "/",
  },
  "hostname": "$(hostname 2>/dev/null || true)",
  "node_name": "$(hostname 2>/dev/null || true)",
  "instance_id": "$(hostname 2>/dev/null || true)-deviceportal",
  "tags": ["jarvis", "portal", "autodiscover", "master"],
  "capabilities": ["portal.autodiscover", "portal.dashboard", "portal.registry"],
}))
PY
)"

curl -fsS --max-time 4 --connect-timeout 2 \
  -H "Content-Type: application/json" \
  -X POST "$PORTAL_AUTODISCOVER_URL" \
  --data-binary "$payload" >/dev/null 2>&1 || true
