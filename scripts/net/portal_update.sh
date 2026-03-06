#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${1:-}"
SERVICE_USER="${2:-}"
SERVICE_NAME="${3:-device-portal.service}"

emit() {
  local key="$1"
  local val="${2:-}"
  printf '%s=%s\n' "$key" "$val"
}

if [[ -z "${REPO_DIR}" || -z "${SERVICE_USER}" ]]; then
  echo "usage: $0 <repo_dir> <service_user> [service_name]" >&2
  exit 2
fi

if [[ ! -d "${REPO_DIR}" || ! -d "${REPO_DIR}/.git" ]]; then
  echo "invalid repo dir: ${REPO_DIR}" >&2
  exit 3
fi

if [[ ! -x "${REPO_DIR}/install/setup_netcontrol.sh" ]]; then
  echo "missing installer: ${REPO_DIR}/install/setup_netcontrol.sh" >&2
  exit 4
fi

if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  echo "invalid service user: ${SERVICE_USER}" >&2
  exit 5
fi

GIT_STATUS="ok"
set +e
GIT_OUT="$(runuser -u "${SERVICE_USER}" -- bash -lc "cd \"${REPO_DIR}\" && git pull --ff-only" 2>&1)"
GIT_RC=$?
set -e
if [[ ${GIT_RC} -ne 0 ]]; then
  GIT_STATUS="failed"
fi

"${REPO_DIR}/install/setup_netcontrol.sh" "${REPO_DIR}" "${SERVICE_USER}" >/dev/null

# restart delayed to let HTTP response complete first
nohup bash -lc "sleep 2; systemctl restart '${SERVICE_NAME}'" >/dev/null 2>&1 &

emit "success" "true"
emit "repo_dir" "${REPO_DIR}"
emit "service_user" "${SERVICE_USER}"
emit "service_name" "${SERVICE_NAME}"
emit "git_status" "${GIT_STATUS}"
emit "restart_scheduled" "true"
emit "message" "Portal update prepared. Service restart scheduled."
emit "details" "$(echo "${GIT_OUT}" | tr '\n' ' ' | sed 's/[[:space:]]\+/ /g')"
