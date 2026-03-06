#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${1:-device-portal.service}"

emit() {
  local key="$1"
  local val="${2:-}"
  printf '%s=%s\n' "$key" "$val"
}

if ! command -v systemctl >/dev/null 2>&1; then
  emit "success" "false"
  emit "code" "systemctl_missing"
  emit "message" "systemctl command not found"
  exit 3
fi

if ! systemctl status "${SERVICE_NAME}" >/dev/null 2>&1; then
  emit "success" "false"
  emit "code" "service_not_found"
  emit "message" "Service not found"
  emit "service_name" "${SERVICE_NAME}"
  exit 4
fi

systemctl restart "${SERVICE_NAME}"

emit "success" "true"
emit "service_name" "${SERVICE_NAME}"
emit "message" "Portal service restart requested"
