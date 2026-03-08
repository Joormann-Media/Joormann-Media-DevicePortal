#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-status}"
SERVICE_NAME="${2:-joormann-media-deviceplayer.service}"

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

load_state="$(systemctl show "${SERVICE_NAME}" --property=LoadState --value 2>/dev/null || true)"
if [[ -z "${load_state}" || "${load_state}" == "not-found" ]]; then
  emit "success" "false"
  emit "code" "service_not_found"
  emit "message" "Service not found"
  emit "service_name" "${SERVICE_NAME}"
  exit 4
fi

case "${ACTION}" in
  start|stop|restart)
    if ! systemctl "${ACTION}" "${SERVICE_NAME}" >/dev/null 2>&1; then
      emit "success" "false"
      emit "code" "service_action_failed"
      emit "message" "Service action failed"
      emit "service_name" "${SERVICE_NAME}"
      emit "action" "${ACTION}"
      exit 5
    fi
    ;;
  status)
    ;;
  *)
    emit "success" "false"
    emit "code" "invalid_action"
    emit "message" "Action must be start|stop|restart|status"
    exit 2
    ;;
esac

active_state="$(systemctl show "${SERVICE_NAME}" --property=ActiveState --value 2>/dev/null || true)"
substate="$(systemctl show "${SERVICE_NAME}" --property=SubState --value 2>/dev/null || true)"
active="false"
if [[ "${active_state}" == "active" ]]; then
  active="true"
fi

emit "success" "true"
emit "service_name" "${SERVICE_NAME}"
emit "action" "${ACTION}"
emit "active" "${active}"
emit "active_state" "${active_state}"
emit "substate" "${substate}"
emit "message" "Player service ${ACTION} processed"
