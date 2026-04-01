#!/usr/bin/env bash
set -euo pipefail

emit() {
  local key="$1"
  local val="${2:-}"
  printf '%s=%s\n' "$key" "$val"
}

REPO_DIR="${1:-}"
SERVICE_USER="${2:-}"
SERVICE_NAME="${3:-device-portal.service}"

if [[ -z "${REPO_DIR}" || -z "${SERVICE_USER}" ]]; then
  emit "success" "false"
  emit "code" "invalid_payload"
  emit "message" "Usage: portal_service_install.sh <repo_dir> <service_user> [service_name]"
  exit 2
fi

if [[ ! -d "${REPO_DIR}" ]]; then
  emit "success" "false"
  emit "code" "repo_not_found"
  emit "message" "Repository directory not found"
  emit "details" "${REPO_DIR}"
  exit 3
fi

if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  emit "success" "false"
  emit "code" "invalid_service_user"
  emit "message" "Service user does not exist"
  emit "details" "${SERVICE_USER}"
  exit 4
fi

INSTALLER="${REPO_DIR}/install/setup_portal.sh"
if [[ ! -x "${INSTALLER}" ]]; then
  emit "success" "false"
  emit "code" "installer_missing"
  emit "message" "Portal installer missing"
  emit "details" "${INSTALLER}"
  exit 5
fi

"${INSTALLER}" "${REPO_DIR}" "${SERVICE_USER}" >/dev/null

ACTIVE_STATE="$(systemctl show "${SERVICE_NAME}" --property=ActiveState --value 2>/dev/null || true)"
SUB_STATE="$(systemctl show "${SERVICE_NAME}" --property=SubState --value 2>/dev/null || true)"
FRAGMENT_PATH="$(systemctl show "${SERVICE_NAME}" --property=FragmentPath --value 2>/dev/null || true)"
EXEC_START="$(systemctl show "${SERVICE_NAME}" --property=ExecStart --value 2>/dev/null || true)"
WORK_DIR="$(systemctl show "${SERVICE_NAME}" --property=WorkingDirectory --value 2>/dev/null || true)"

emit "success" "true"
emit "code" "ok"
emit "message" "Portal service installed"
emit "repo_dir" "${REPO_DIR}"
emit "service_user" "${SERVICE_USER}"
emit "service_name" "${SERVICE_NAME}"
emit "active_state" "${ACTIVE_STATE}"
emit "substate" "${SUB_STATE}"
emit "fragment_path" "${FRAGMENT_PATH}"
emit "exec_start" "${EXEC_START}"
emit "working_directory" "${WORK_DIR}"
