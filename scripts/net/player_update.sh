#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-start}"
if [[ "${MODE}" != "start" ]]; then
  echo "usage: $0 start <player_repo_dir> <service_user> [service_name] [update_dir]" >&2
  exit 2
fi

shift || true
PLAYER_REPO_DIR="${1:-}"
SERVICE_USER="${2:-}"
SERVICE_NAME="${3:-joormann-media-deviceplayer.service}"
UPDATE_DIR="${4:-/tmp/deviceplayer-updates}"

emit() {
  local key="$1"
  local val="${2:-}"
  printf '%s=%s\n' "$key" "$val"
}

utc_now() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

if [[ -z "${PLAYER_REPO_DIR}" || -z "${SERVICE_USER}" ]]; then
  echo "usage: $0 start <player_repo_dir> <service_user> [service_name] [update_dir]" >&2
  exit 2
fi

if [[ ! -d "${PLAYER_REPO_DIR}" || ! -d "${PLAYER_REPO_DIR}/.git" ]]; then
  echo "invalid player repo dir: ${PLAYER_REPO_DIR}" >&2
  exit 3
fi

if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  echo "invalid service user: ${SERVICE_USER}" >&2
  exit 4
fi

SERVICE_GROUP="$(id -gn "${SERVICE_USER}" 2>/dev/null || echo "${SERVICE_USER}")"

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
repo_dir=${PLAYER_REPO_DIR}
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
  echo "[player-update] start job=${JOB_ID} repo=${PLAYER_REPO_DIR} user=${SERVICE_USER}"

  BEFORE_COMMIT="$(runuser -u "${SERVICE_USER}" -- bash -lc "cd \"${PLAYER_REPO_DIR}\" && git rev-parse --short=12 HEAD" 2>/dev/null || true)"
  if [[ -n "${BEFORE_COMMIT}" ]]; then
    echo "[git] before=${BEFORE_COMMIT}"
  fi

  GIT_OUT="$(runuser -u "${SERVICE_USER}" -- bash -lc "cd \"${PLAYER_REPO_DIR}\" && git pull --ff-only" 2>&1)"
  GIT_RC=$?
  AFTER_COMMIT="$(runuser -u "${SERVICE_USER}" -- bash -lc "cd \"${PLAYER_REPO_DIR}\" && git rev-parse --short=12 HEAD" 2>/dev/null || true)"
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
repo_dir=${PLAYER_REPO_DIR}
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

  echo "[apt] install runtime dependencies"
  apt-get update
  apt-get install -y python3 python3-venv python3-pip libsdl2-dev
  APT_RC=$?
  if [[ ${APT_RC} -ne 0 ]]; then
    echo "[apt] failed rc=${APT_RC}"
    cat > "${STATE_FILE}" <<EOF
status=failed
success=false
git_status=${GIT_STATUS}
repo_dir=${PLAYER_REPO_DIR}
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

  VENV_DIR="${PLAYER_REPO_DIR}/.venv"
  REQ_FILE="${PLAYER_REPO_DIR}/requirements.txt"

  if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    echo "[venv] create ${VENV_DIR}"
    runuser -u "${SERVICE_USER}" -- python3 -m venv "${VENV_DIR}"
  fi

  echo "[pip] install requirements"
  if [[ -f "${REQ_FILE}" ]]; then
    runuser -u "${SERVICE_USER}" -- "${VENV_DIR}/bin/pip" install -r "${REQ_FILE}"
  else
    runuser -u "${SERVICE_USER}" -- "${VENV_DIR}/bin/pip" install pygame-ce
  fi
  PIP_RC=$?
  if [[ ${PIP_RC} -ne 0 ]]; then
    echo "[pip] failed rc=${PIP_RC}"
    cat > "${STATE_FILE}" <<EOF
status=failed
success=false
git_status=${GIT_STATUS}
repo_dir=${PLAYER_REPO_DIR}
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

  SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}"
  ENV_FILE="/etc/default/jm-deviceplayer"

  cat > "${ENV_FILE}" <<EOF
PYTHONUNBUFFERED=1
DEVICEPLAYER_MANIFEST_PATH=/mnt/deviceportal/media/stream/current/manifest.json
EOF
  chmod 0644 "${ENV_FILE}" || true

  cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=Joormann Media DevicePlayer
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${PLAYER_REPO_DIR}
EnvironmentFile=-${ENV_FILE}
Environment=SDL_AUDIODRIVER=dummy
ExecStart=${VENV_DIR}/bin/python ${PLAYER_REPO_DIR}/run.py
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

  echo "[service] daemon-reload + enable + restart ${SERVICE_NAME}"
  systemctl daemon-reload
  systemctl enable "${SERVICE_NAME}"

  cat > "${STATE_FILE}" <<EOF
status=restarting
success=false
git_status=${GIT_STATUS}
repo_dir=${PLAYER_REPO_DIR}
service_user=${SERVICE_USER}
service_name=${SERVICE_NAME}
job_id=${JOB_ID}
started_at=${STARTED_AT}
updated_at=$(utc_now)
finished_at=
before_commit=${BEFORE_COMMIT}
after_commit=${AFTER_COMMIT}
EOF

  systemctl restart "${SERVICE_NAME}"
  SRV_RC=$?
  if [[ ${SRV_RC} -eq 0 ]]; then
    cat > "${STATE_FILE}" <<EOF
status=done
success=true
git_status=${GIT_STATUS}
repo_dir=${PLAYER_REPO_DIR}
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
repo_dir=${PLAYER_REPO_DIR}
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

  exit 0
) >"${LOG_FILE}" 2>&1 &

emit "success" "true"
emit "code" "ok"
emit "message" "Player update started"
emit "job_id" "${JOB_ID}"
emit "repo_dir" "${PLAYER_REPO_DIR}"
emit "service_user" "${SERVICE_USER}"
emit "service_name" "${SERVICE_NAME}"
emit "log_file" "${LOG_FILE}"
emit "state_file" "${STATE_FILE}"
