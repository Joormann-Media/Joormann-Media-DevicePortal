#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-}"

emit() {
  local key="$1"
  local val="${2:-}"
  printf '%s=%s\n' "$key" "$val"
}

if [[ "${ACTION}" != "shutdown" && "${ACTION}" != "reboot" ]]; then
  emit "success" "false"
  emit "code" "invalid_action"
  emit "message" "Action must be shutdown or reboot"
  exit 2
fi

if ! command -v systemctl >/dev/null 2>&1; then
  emit "success" "false"
  emit "code" "systemctl_missing"
  emit "message" "systemctl command not found"
  exit 3
fi

if [[ "${ACTION}" == "shutdown" ]]; then
  systemctl poweroff --no-wall
  emit "success" "true"
  emit "action" "shutdown"
  emit "message" "Shutdown requested"
else
  systemctl reboot --no-wall
  emit "success" "true"
  emit "action" "reboot"
  emit "message" "Reboot requested"
fi
