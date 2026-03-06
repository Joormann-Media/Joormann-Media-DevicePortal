#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-start}"
shift || true

emit() {
  local key="$1"
  local val="${2:-}"
  printf '%s=%s\n' "$key" "$val"
}

utc_now() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

if [[ "${MODE}" != "start" ]]; then
  echo "usage: $0 start <repo_dir> <service_user> [service_name] [update_dir]" >&2
  exit 2
fi

REPO_DIR="${1:-}"
SERVICE_USER="${2:-}"
SERVICE_NAME="${3:-device-portal.service}"
UPDATE_DIR="${4:-/tmp/deviceportal-updates}"

if [[ -z "${REPO_DIR}" || -z "${SERVICE_USER}" ]]; then
  echo "usage: $0 start <repo_dir> <service_user> [service_name] [update_dir]" >&2
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

mkdir -p "${UPDATE_DIR}"
chmod 0755 "${UPDATE_DIR}" || true
JOB_ID="$(date +%Y%m%d%H%M%S)-$$"
LOG_FILE="${UPDATE_DIR}/${JOB_ID}.log"
STATE_FILE="${UPDATE_DIR}/${JOB_ID}.state"
touch "${LOG_FILE}" "${STATE_FILE}"
chmod 0644 "${LOG_FILE}" "${STATE_FILE}" || true
STARTED_AT="$(utc_now)"

cat > "${STATE_FILE}" <<EOF
status=running
success=false
git_status=unknown
repo_dir=${REPO_DIR}
service_user=${SERVICE_USER}
service_name=${SERVICE_NAME}
job_id=${JOB_ID}
started_at=${STARTED_AT}
updated_at=${STARTED_AT}
finished_at=
before_commit=
after_commit=
EOF

(
  set +e
  echo "[update] start job=${JOB_ID} repo=${REPO_DIR} user=${SERVICE_USER}"
  BEFORE_COMMIT="$(runuser -u "${SERVICE_USER}" -- bash -lc "cd \"${REPO_DIR}\" && git rev-parse --short=12 HEAD" 2>/dev/null || true)"
  if [[ -n "${BEFORE_COMMIT}" ]]; then
    echo "[git] before=${BEFORE_COMMIT}"
  fi

  GIT_OUT="$(runuser -u "${SERVICE_USER}" -- bash -lc "cd \"${REPO_DIR}\" && git pull --ff-only" 2>&1)"
  GIT_RC=$?
  AFTER_COMMIT="$(runuser -u "${SERVICE_USER}" -- bash -lc "cd \"${REPO_DIR}\" && git rev-parse --short=12 HEAD" 2>/dev/null || true)"
  if [[ ${GIT_RC} -eq 0 ]]; then
    GIT_STATUS="ok"
    echo "[git] ok"
  else
    GIT_STATUS="failed"
    echo "[git] failed"
  fi
  echo "${GIT_OUT}"

  if [[ ${GIT_RC} -ne 0 ]]; then
    cat > "${STATE_FILE}" <<EOF
status=failed
success=false
git_status=${GIT_STATUS}
repo_dir=${REPO_DIR}
service_user=${SERVICE_USER}
service_name=${SERVICE_NAME}
job_id=${JOB_ID}
started_at=${STARTED_AT}
updated_at=$(utc_now)
finished_at=$(utc_now)
before_commit=${BEFORE_COMMIT}
after_commit=${AFTER_COMMIT}
EOF
    exit 0
  fi

  echo "[netcontrol] deploying wrappers"
  "${REPO_DIR}/install/setup_netcontrol.sh" "${REPO_DIR}" "${SERVICE_USER}"
  NET_RC=$?
  if [[ ${NET_RC} -ne 0 ]]; then
    echo "[netcontrol] failed rc=${NET_RC}"
    cat > "${STATE_FILE}" <<EOF
status=failed
success=false
git_status=${GIT_STATUS}
repo_dir=${REPO_DIR}
service_user=${SERVICE_USER}
service_name=${SERVICE_NAME}
job_id=${JOB_ID}
started_at=${STARTED_AT}
updated_at=$(utc_now)
finished_at=$(utc_now)
before_commit=${BEFORE_COMMIT}
after_commit=${AFTER_COMMIT}
EOF
    exit 0
  fi

  cat > "${STATE_FILE}" <<EOF
status=restarting
success=false
git_status=${GIT_STATUS}
repo_dir=${REPO_DIR}
service_user=${SERVICE_USER}
service_name=${SERVICE_NAME}
job_id=${JOB_ID}
started_at=${STARTED_AT}
updated_at=$(utc_now)
finished_at=
before_commit=${BEFORE_COMMIT}
after_commit=${AFTER_COMMIT}
EOF

  echo "[service] restarting ${SERVICE_NAME}"
  systemctl restart "${SERVICE_NAME}"
  SRV_RC=$?
  if [[ ${SRV_RC} -eq 0 ]]; then
    cat > "${STATE_FILE}" <<EOF
status=done
success=true
git_status=${GIT_STATUS}
repo_dir=${REPO_DIR}
service_user=${SERVICE_USER}
service_name=${SERVICE_NAME}
job_id=${JOB_ID}
started_at=${STARTED_AT}
updated_at=$(utc_now)
finished_at=$(utc_now)
before_commit=${BEFORE_COMMIT}
after_commit=${AFTER_COMMIT}
EOF
    echo "[service] restart ok"
  else
    cat > "${STATE_FILE}" <<EOF
status=failed
success=false
git_status=${GIT_STATUS}
repo_dir=${REPO_DIR}
service_user=${SERVICE_USER}
service_name=${SERVICE_NAME}
job_id=${JOB_ID}
started_at=${STARTED_AT}
updated_at=$(utc_now)
finished_at=$(utc_now)
before_commit=${BEFORE_COMMIT}
after_commit=${AFTER_COMMIT}
EOF
    echo "[service] restart failed rc=${SRV_RC}"
  fi
) >> "${LOG_FILE}" 2>&1 &

emit "success" "true"
emit "job_id" "${JOB_ID}"
emit "repo_dir" "${REPO_DIR}"
emit "service_user" "${SERVICE_USER}"
emit "service_name" "${SERVICE_NAME}"
emit "restart_scheduled" "true"
emit "started_at" "${STARTED_AT}"
emit "message" "Portal update started. Live log available."
emit "log_file" "${LOG_FILE}"
