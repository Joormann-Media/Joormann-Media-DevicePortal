#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-start}"
if [[ "${MODE}" != "start" ]]; then
  echo "usage: $0 start <player_repo_link_or_path> <service_user> [service_name] [update_dir] [portal_repo_dir]" >&2
  exit 2
fi

shift || true
PLAYER_REPO_REF="${1:-}"
SERVICE_USER="${2:-}"
SERVICE_NAME="${3:-joormann-media-deviceplayer.service}"
UPDATE_DIR="${4:-/tmp/deviceplayer-updates}"
PORTAL_REPO_DIR="${5:-}"

emit() {
  local key="$1"
  local val="${2:-}"
  printf '%s=%s\n' "$key" "$val"
}

utc_now() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

is_repo_url() {
  local v="$1"
  [[ "$v" =~ ^https?:// ]] || [[ "$v" =~ ^git@ ]] || [[ "$v" =~ ^ssh:// ]]
}

repo_name_from_ref() {
  local v="$1"
  local b
  b="$(basename "$v")"
  b="${b%.git}"
  if [[ -z "$b" ]]; then
    b="Joormann-Media-DevicePlayer"
  fi
  printf '%s' "$b"
}

resolve_portal_storage_config_path() {
  local portal_repo="$1"
  if [[ -z "${portal_repo}" ]]; then
    return
  fi
  local cfg="${portal_repo}/var/data/config-storage.json"
  if [[ -f "${cfg}" ]]; then
    printf '%s' "${cfg}"
  fi
}

resolve_manifest_path_from_storage_config() {
  local cfg_path="$1"
  if [[ -z "${cfg_path}" || ! -f "${cfg_path}" ]]; then
    return
  fi
  python3 - "$cfg_path" <<'PY'
import json
import sys
from pathlib import Path

cfg_path = Path(sys.argv[1])
try:
    payload = json.loads(cfg_path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(0)

rel = Path("stream/current/manifest.json")
internal = payload.get("internal") if isinstance(payload, dict) else {}
if isinstance(internal, dict):
    if bool(internal.get("allow_media_storage", True)):
        mount = str(internal.get("mount_path") or "").strip()
        if mount:
            print(str(Path(mount) / rel))
            raise SystemExit(0)

devices = payload.get("devices") if isinstance(payload, dict) else []
if isinstance(devices, list):
    for item in devices:
        if not isinstance(item, dict):
            continue
        if not bool(item.get("allow_media_storage", False)):
            continue
        mount = str(item.get("mount_path") or "").strip()
        if mount:
            print(str(Path(mount) / rel))
            raise SystemExit(0)
PY
}

ensure_user_groups() {
  local user_name="$1"
  local groups=(video render input audio)
  local existing=()
  local grp
  for grp in "${groups[@]}"; do
    if getent group "${grp}" >/dev/null 2>&1; then
      existing+=("${grp}")
    fi
  done
  if [[ ${#existing[@]} -gt 0 ]]; then
    echo "[perm] ensure ${user_name} supplementary groups: ${existing[*]}"
    usermod -aG "$(IFS=,; echo "${existing[*]}")" "${user_name}" || true
  fi
}

service_supplementary_groups() {
  local groups=(video render input audio)
  local existing=()
  local grp
  for grp in "${groups[@]}"; do
    if getent group "${grp}" >/dev/null 2>&1; then
      existing+=("${grp}")
    fi
  done
  if [[ ${#existing[@]} -eq 0 ]]; then
    return
  fi
  printf '%s' "${existing[*]}"
}

if [[ -z "${PLAYER_REPO_REF}" || -z "${SERVICE_USER}" ]]; then
  echo "usage: $0 start <player_repo_link_or_path> <service_user> [service_name] [update_dir] [portal_repo_dir]" >&2
  exit 2
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
repo_ref=${PLAYER_REPO_REF}
repo_dir=
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
  echo "[player-update] start job=${JOB_ID} ref=${PLAYER_REPO_REF} user=${SERVICE_USER}"

  REPO_REF="${PLAYER_REPO_REF}"
  if is_repo_url "${REPO_REF}"; then
    if [[ -n "${PORTAL_REPO_DIR}" ]]; then
      PORTAL_PARENT="$(dirname "${PORTAL_REPO_DIR}")"
    else
      PORTAL_PARENT="$(eval echo "~${SERVICE_USER}")/projects"
    fi
    mkdir -p "${PORTAL_PARENT}"
    REPO_NAME="$(repo_name_from_ref "${REPO_REF}")"
    REPO_DIR="${PORTAL_PARENT}/${REPO_NAME}"

    if [[ -d "${REPO_DIR}/.git" ]]; then
      echo "[git] existing clone found at ${REPO_DIR}"
    elif [[ -e "${REPO_DIR}" ]]; then
      echo "[git] target path exists but is not a git repo: ${REPO_DIR}"
      cat > "${STATE_FILE}" <<EOF
status=failed
success=false
git_status=failed
repo_ref=${REPO_REF}
repo_dir=${REPO_DIR}
service_user=${SERVICE_USER}
service_name=${SERVICE_NAME}
job_id=${JOB_ID}
started_at=${STARTED_AT}
updated_at=$(utc_now)
finished_at=$(utc_now)
before_commit=
after_commit=
EOF
      exit 0
    else
      echo "[git] cloning ${REPO_REF} -> ${REPO_DIR}"
      runuser -u "${SERVICE_USER}" -- bash -lc "git clone --depth=1 \"${REPO_REF}\" \"${REPO_DIR}\""
      CLONE_RC=$?
      if [[ ${CLONE_RC} -ne 0 ]]; then
        echo "[git] clone failed rc=${CLONE_RC}"
        cat > "${STATE_FILE}" <<EOF
status=failed
success=false
git_status=failed
repo_ref=${REPO_REF}
repo_dir=${REPO_DIR}
service_user=${SERVICE_USER}
service_name=${SERVICE_NAME}
job_id=${JOB_ID}
started_at=${STARTED_AT}
updated_at=$(utc_now)
finished_at=$(utc_now)
before_commit=
after_commit=
EOF
        exit 0
      fi
    fi
  else
    REPO_DIR="${REPO_REF}"
    if [[ ! -d "${REPO_DIR}/.git" ]]; then
      echo "[git] invalid local player repo dir: ${REPO_DIR}"
      cat > "${STATE_FILE}" <<EOF
status=failed
success=false
git_status=failed
repo_ref=${REPO_REF}
repo_dir=${REPO_DIR}
service_user=${SERVICE_USER}
service_name=${SERVICE_NAME}
job_id=${JOB_ID}
started_at=${STARTED_AT}
updated_at=$(utc_now)
finished_at=$(utc_now)
before_commit=
after_commit=
EOF
      exit 0
    fi
  fi

  if [[ -e "${REPO_DIR}" ]]; then
    chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${REPO_DIR}" || true
    find "${REPO_DIR}" -type d -exec chmod u+rwx,go+rx {} + || true
  fi
  ensure_user_groups "${SERVICE_USER}"

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
repo_ref=${REPO_REF}
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
repo_ref=${REPO_REF}
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

  VENV_DIR="${REPO_DIR}/.venv"
  REQ_FILE="${REPO_DIR}/requirements.txt"

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
repo_ref=${REPO_REF}
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

  SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}"
  SERVICE_DROPIN_DIR="/etc/systemd/system/${SERVICE_NAME}.d"
  SERVICE_DROPIN_FILE="${SERVICE_DROPIN_DIR}/10-deviceplayer-permissions.conf"
  ENV_FILE="/etc/default/jm-deviceplayer"
  PORTAL_STORAGE_CONFIG_PATH="$(resolve_portal_storage_config_path "${PORTAL_REPO_DIR}")"
  if [[ -z "${PORTAL_STORAGE_CONFIG_PATH}" ]]; then
    PORTAL_STORAGE_CONFIG_PATH="${PORTAL_REPO_DIR}/var/data/config-storage.json"
  fi

  cat > "${ENV_FILE}" <<EOF
PYTHONUNBUFFERED=1
DEVICEPLAYER_MANIFEST_PATH=
DEVICEPLAYER_STORAGE_ROOT=
DEVICEPLAYER_PORTAL_STORAGE_CONFIG=${PORTAL_STORAGE_CONFIG_PATH}
DEVICEPLAYER_VIDEO_DRIVERS=kmsdrm,fbcon,wayland,x11
EOF
  chmod 0644 "${ENV_FILE}" || true
  echo "[env] DEVICEPLAYER_MANIFEST_PATH="
  echo "[env] DEVICEPLAYER_STORAGE_ROOT="
  echo "[env] DEVICEPLAYER_PORTAL_STORAGE_CONFIG=${PORTAL_STORAGE_CONFIG_PATH}"

  cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=Joormann Media DevicePlayer
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${REPO_DIR}
EnvironmentFile=-${ENV_FILE}
Environment=SDL_AUDIODRIVER=dummy
Environment=DEVICEPLAYER_MANIFEST_PATH=
Environment=DEVICEPLAYER_STORAGE_ROOT=
ExecStart=${VENV_DIR}/bin/python ${REPO_DIR}/run.py
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF
  chmod 0644 "${SERVICE_FILE}" || true

  SUPP_GROUPS="$(service_supplementary_groups)"
  mkdir -p "${SERVICE_DROPIN_DIR}"
  if [[ -n "${SUPP_GROUPS}" ]]; then
    cat > "${SERVICE_DROPIN_FILE}" <<EOF
[Service]
SupplementaryGroups=${SUPP_GROUPS}
EOF
    chmod 0644 "${SERVICE_DROPIN_FILE}" || true
    echo "[service] supplementary groups set: ${SUPP_GROUPS}"
  else
    rm -f "${SERVICE_DROPIN_FILE}" || true
  fi
  echo "[preflight] /dev/dri:"
  ls -lah /dev/dri 2>/dev/null || echo "[preflight] /dev/dri not present"
  echo "[preflight] service user groups: $(id -nG "${SERVICE_USER}" 2>/dev/null || true)"

  echo "[service] daemon-reload + enable + restart ${SERVICE_NAME}"
  systemctl daemon-reload
  systemctl enable "${SERVICE_NAME}"

  cat > "${STATE_FILE}" <<EOF
status=restarting
success=false
git_status=${GIT_STATUS}
repo_ref=${REPO_REF}
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

  systemctl restart "${SERVICE_NAME}"
  SRV_RC=$?
  if [[ ${SRV_RC} -eq 0 ]]; then
    cat > "${STATE_FILE}" <<EOF
status=done
success=true
git_status=${GIT_STATUS}
repo_ref=${REPO_REF}
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
repo_ref=${REPO_REF}
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

  exit 0
) >"${LOG_FILE}" 2>&1 &

emit "success" "true"
emit "code" "ok"
emit "message" "Player update started"
emit "job_id" "${JOB_ID}"
emit "repo_link" "${PLAYER_REPO_REF}"
emit "repo_dir" ""
emit "service_user" "${SERVICE_USER}"
emit "service_name" "${SERVICE_NAME}"
emit "log_file" "${LOG_FILE}"
emit "state_file" "${STATE_FILE}"
