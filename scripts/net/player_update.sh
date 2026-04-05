#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-start}"
shift || true
PLAYER_REPO_REF="${1:-}"
SERVICE_USER="${2:-}"
SERVICE_NAME="${3:-joormann-media-deviceplayer.service}"
INSTALL_DIR="${4:-}"
UPDATE_DIR="${5:-/tmp/deviceplayer-updates}"
PORTAL_REPO_DIR="${6:-}"
USE_SERVICE="${7:-true}"
AUTOSTART="${8:-true}"

# Backward compatibility:
# old signature: start <repo> <user> [service] [update_dir] [portal_repo_dir]
if [[ -z "${6:-}" && -n "${4:-}" ]]; then
  case "${4}" in
    /tmp/*updates*|/var/*updates*|*updates-player*|*updates*)
      INSTALL_DIR=""
      UPDATE_DIR="${4}"
      PORTAL_REPO_DIR="${5:-}"
      ;;
  esac
fi

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

str_true() {
  local v
  v="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
  [[ "${v}" == "1" || "${v}" == "true" || "${v}" == "yes" || "${v}" == "on" ]]
}

service_env_file_from_name() {
  local svc="${1:-device-service}"
  local slug
  slug="$(printf '%s' "${svc}" | sed 's/\.service$//' | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9_-]/-/g')"
  if [[ -z "${slug}" ]]; then
    slug="device-service"
  fi
  printf '/etc/default/jm-%s' "${slug}"
}

service_slug_from_name() {
  local svc="${1:-device-service}"
  local slug
  slug="$(printf '%s' "${svc}" | sed 's/\.service$//' | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9_-]/-/g')"
  if [[ -z "${slug}" ]]; then
    slug="device-service"
  fi
  printf '%s' "${slug}"
}

service_wrapper_start_path() {
  local slug
  slug="$(service_slug_from_name "${1:-device-service}")"
  printf '/opt/deviceportal/bin/jm-managed-%s-start.sh' "${slug}"
}

service_wrapper_stop_path() {
  local slug
  slug="$(service_slug_from_name "${1:-device-service}")"
  printf '/opt/deviceportal/bin/jm-managed-%s-stop.sh' "${slug}"
}

resolve_repo_dir() {
  local ref="$1"
  local user="$2"
  local install_dir="$3"
  local portal_repo_dir="$4"

  if [[ -n "${install_dir}" ]]; then
    printf '%s' "${install_dir}"
    return
  fi

  if is_repo_url "${ref}"; then
    local parent name
    if [[ -n "${portal_repo_dir}" ]]; then
      parent="$(dirname "${portal_repo_dir}")"
    else
      parent="$(eval echo "~${user}")/projects"
    fi
    name="$(repo_name_from_ref "${ref}")"
    printf '%s/%s' "${parent}" "${name}"
    return
  fi

  printf '%s' "${ref}"
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

if [[ "${MODE}" == "service-autostart" ]]; then
  if [[ -z "${PLAYER_REPO_REF}" ]]; then
    emit "success" "false"
    emit "code" "service_name_missing"
    emit "message" "Service name is required."
    exit 2
  fi
  if str_true "${SERVICE_USER}"; then
    systemctl enable "${PLAYER_REPO_REF}" >/dev/null 2>&1 || true
    emit "success" "true"
    emit "code" "ok"
    emit "message" "Autostart enabled"
    emit "service_name" "${PLAYER_REPO_REF}"
    emit "autostart" "true"
    exit 0
  fi
  systemctl disable "${PLAYER_REPO_REF}" >/dev/null 2>&1 || true
  emit "success" "true"
  emit "code" "ok"
  emit "message" "Autostart disabled"
  emit "service_name" "${PLAYER_REPO_REF}"
  emit "autostart" "false"
  exit 0
fi

if [[ "${MODE}" == "uninstall" ]]; then
  # uninstall <repo_ref> <service_user> [service_name] [install_dir] [portal_repo_dir] [remove_repo]
  REMOVE_REPO="${7:-false}"
  if [[ -z "${PLAYER_REPO_REF}" || -z "${SERVICE_USER}" ]]; then
    emit "success" "false"
    emit "code" "invalid_args"
    emit "message" "usage: $0 uninstall <repo_ref> <service_user> [service_name] [install_dir] [portal_repo_dir] [remove_repo]"
    exit 2
  fi
  REPO_DIR="$(resolve_repo_dir "${PLAYER_REPO_REF}" "${SERVICE_USER}" "${INSTALL_DIR}" "${PORTAL_REPO_DIR}")"
  if [[ -n "${SERVICE_NAME}" ]]; then
    START_WRAPPER="$(service_wrapper_start_path "${SERVICE_NAME}")"
    STOP_WRAPPER="$(service_wrapper_stop_path "${SERVICE_NAME}")"
    systemctl stop "${SERVICE_NAME}" >/dev/null 2>&1 || true
    systemctl disable "${SERVICE_NAME}" >/dev/null 2>&1 || true
    rm -f "/etc/systemd/system/${SERVICE_NAME}" || true
    rm -rf "/etc/systemd/system/${SERVICE_NAME}.d" || true
    rm -f "${START_WRAPPER}" "${STOP_WRAPPER}" || true
    systemctl daemon-reload >/dev/null 2>&1 || true
  fi
  REMOVED_REPO="false"
  if str_true "${REMOVE_REPO}" && [[ -d "${REPO_DIR}" ]]; then
    rm -rf "${REPO_DIR}" || true
    if [[ ! -d "${REPO_DIR}" ]]; then
      REMOVED_REPO="true"
    fi
  fi
  emit "success" "true"
  emit "code" "ok"
  emit "message" "Uninstall finished"
  emit "repo_dir" "${REPO_DIR}"
  emit "service_name" "${SERVICE_NAME}"
  emit "removed_repo" "${REMOVED_REPO}"
  exit 0
fi

if [[ "${MODE}" != "start" ]]; then
  echo "usage: $0 start|uninstall|service-autostart ..." >&2
  exit 2
fi

if [[ -z "${PLAYER_REPO_REF}" || -z "${SERVICE_USER}" ]]; then
  echo "usage: $0 start <player_repo_link_or_path> <service_user> [service_name] [install_dir] [update_dir] [portal_repo_dir] [use_service] [autostart]" >&2
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
install_dir=${INSTALL_DIR}
use_service=${USE_SERVICE}
autostart=${AUTOSTART}
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
  REPO_DIR="$(resolve_repo_dir "${REPO_REF}" "${SERVICE_USER}" "${INSTALL_DIR}" "${PORTAL_REPO_DIR}")"
  if is_repo_url "${REPO_REF}"; then
    mkdir -p "$(dirname "${REPO_DIR}")"

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
install_dir=${INSTALL_DIR}
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
install_dir=${INSTALL_DIR}
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
install_dir=${INSTALL_DIR}
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
install_dir=${INSTALL_DIR}
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
install_dir=${INSTALL_DIR}
job_id=${JOB_ID}
started_at=${STARTED_AT}
updated_at=$(utc_now)
finished_at=$(utc_now)
before_commit=${BEFORE_COMMIT}
after_commit=${AFTER_COMMIT}
EOF
    exit 0
  fi

  START_SCRIPT="${REPO_DIR}/scripts/start-dev.sh"
  STOP_SCRIPT="${REPO_DIR}/scripts/stop-dev.sh"
  SCRIPT_MANAGED="false"
  if [[ -f "${START_SCRIPT}" ]]; then
    SCRIPT_MANAGED="true"
  fi

  VENV_DIR="${REPO_DIR}/.venv"
  REQ_FILE="${REPO_DIR}/requirements.txt"
  if [[ "${SCRIPT_MANAGED}" != "true" ]]; then
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
install_dir=${INSTALL_DIR}
job_id=${JOB_ID}
started_at=${STARTED_AT}
updated_at=$(utc_now)
finished_at=$(utc_now)
before_commit=${BEFORE_COMMIT}
after_commit=${AFTER_COMMIT}
EOF
      exit 0
    fi
  else
    echo "[install] repo provides scripts/start-dev.sh; dependency/install handled by repo scripts"
  fi

  if str_true "${USE_SERVICE}"; then
    APP_ENTRY=""
    if [[ "${SCRIPT_MANAGED}" != "true" ]]; then
      for candidate in "run.py" "app.py" "main.py" "server.py"; do
        if [[ -f "${REPO_DIR}/${candidate}" ]]; then
          APP_ENTRY="${REPO_DIR}/${candidate}"
          break
        fi
      done
      if [[ -z "${APP_ENTRY}" ]]; then
        echo "[service] no supported entrypoint found (run.py/app.py/main.py/server.py)"
        cat > "${STATE_FILE}" <<EOF
status=failed
success=false
git_status=${GIT_STATUS}
repo_ref=${REPO_REF}
repo_dir=${REPO_DIR}
service_user=${SERVICE_USER}
service_name=${SERVICE_NAME}
install_dir=${INSTALL_DIR}
use_service=${USE_SERVICE}
autostart=${AUTOSTART}
job_id=${JOB_ID}
started_at=${STARTED_AT}
updated_at=$(utc_now)
finished_at=$(utc_now)
before_commit=${BEFORE_COMMIT}
after_commit=${AFTER_COMMIT}
EOF
        exit 0
      fi
    fi

    START_WRAPPER="$(service_wrapper_start_path "${SERVICE_NAME}")"
    STOP_WRAPPER="$(service_wrapper_stop_path "${SERVICE_NAME}")"
    SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}"
    SERVICE_DROPIN_DIR="/etc/systemd/system/${SERVICE_NAME}.d"
    SERVICE_DROPIN_FILE="${SERVICE_DROPIN_DIR}/10-deviceplayer-permissions.conf"
    ENV_FILE="$(service_env_file_from_name "${SERVICE_NAME}")"
    PORTAL_STORAGE_CONFIG_PATH="$(resolve_portal_storage_config_path "${PORTAL_REPO_DIR}")"
    if [[ -z "${PORTAL_STORAGE_CONFIG_PATH}" ]]; then
      PORTAL_STORAGE_CONFIG_PATH="${PORTAL_REPO_DIR}/var/data/config-storage.json"
    fi
    PORTAL_PLAYER_SOURCE_PATH="${PORTAL_REPO_DIR}/var/data/player-source.json"

    cat > "${ENV_FILE}" <<EOF
PYTHONUNBUFFERED=1
DEVICEPLAYER_MANIFEST_PATH=
DEVICEPLAYER_STORAGE_ROOT=
DEVICEPLAYER_PORTAL_PLAYER_SOURCE=${PORTAL_PLAYER_SOURCE_PATH}
DEVICEPLAYER_PORTAL_STORAGE_CONFIG=${PORTAL_STORAGE_CONFIG_PATH}
DEVICEPLAYER_VIDEO_DRIVERS=kmsdrm,fbcon,wayland,x11
EOF
    chmod 0644 "${ENV_FILE}" || true
    echo "[env] DEVICEPLAYER_MANIFEST_PATH="
    echo "[env] DEVICEPLAYER_STORAGE_ROOT="
    echo "[env] DEVICEPLAYER_PORTAL_PLAYER_SOURCE=${PORTAL_PLAYER_SOURCE_PATH}"
    echo "[env] DEVICEPLAYER_PORTAL_STORAGE_CONFIG=${PORTAL_STORAGE_CONFIG_PATH}"

    if [[ "${SCRIPT_MANAGED}" == "true" ]]; then
      cat > "${START_WRAPPER}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "${REPO_DIR}"
exec /usr/bin/env bash "${START_SCRIPT}"
EOF
      chmod 0755 "${START_WRAPPER}" || true

      if [[ -f "${STOP_SCRIPT}" ]]; then
        cat > "${STOP_WRAPPER}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "${REPO_DIR}"
exec /usr/bin/env bash "${STOP_SCRIPT}"
EOF
      else
        cat > "${STOP_WRAPPER}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exit 0
EOF
      fi
      chmod 0755 "${STOP_WRAPPER}" || true
    else
      cat > "${START_WRAPPER}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "${REPO_DIR}"
exec "${VENV_DIR}/bin/python" "${APP_ENTRY}"
EOF
      chmod 0755 "${START_WRAPPER}" || true
      cat > "${STOP_WRAPPER}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exit 0
EOF
      chmod 0755 "${STOP_WRAPPER}" || true
    fi

    if [[ "${SCRIPT_MANAGED}" == "true" ]]; then
      cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=Joormann Media Managed Service (${SERVICE_NAME})
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${REPO_DIR}
EnvironmentFile=-${ENV_FILE}
ExecStart=${START_WRAPPER}
ExecStop=${STOP_WRAPPER}
TimeoutStartSec=0
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF
    else
      cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=Joormann Media Managed Service (${SERVICE_NAME})
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
Environment=DEVICEPLAYER_PORTAL_PLAYER_SOURCE=${PORTAL_PLAYER_SOURCE_PATH}
ExecStart=${START_WRAPPER}
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF
    fi
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

    echo "[service] daemon-reload + restart ${SERVICE_NAME}"
    systemctl daemon-reload
    if str_true "${AUTOSTART}"; then
      systemctl enable "${SERVICE_NAME}"
    else
      systemctl disable "${SERVICE_NAME}" >/dev/null 2>&1 || true
    fi

    cat > "${STATE_FILE}" <<EOF
status=restarting
success=false
git_status=${GIT_STATUS}
repo_ref=${REPO_REF}
repo_dir=${REPO_DIR}
service_user=${SERVICE_USER}
service_name=${SERVICE_NAME}
install_dir=${INSTALL_DIR}
use_service=${USE_SERVICE}
autostart=${AUTOSTART}
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
install_dir=${INSTALL_DIR}
use_service=${USE_SERVICE}
autostart=${AUTOSTART}
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
install_dir=${INSTALL_DIR}
use_service=${USE_SERVICE}
autostart=${AUTOSTART}
job_id=${JOB_ID}
started_at=${STARTED_AT}
updated_at=$(utc_now)
finished_at=$(utc_now)
before_commit=${BEFORE_COMMIT}
after_commit=${AFTER_COMMIT}
EOF
      echo "[service] restart failed rc=${SRV_RC}"
    fi
  else
    echo "[service] skipped (use_service=false)"
    cat > "${STATE_FILE}" <<EOF
status=done
success=true
git_status=${GIT_STATUS}
repo_ref=${REPO_REF}
repo_dir=${REPO_DIR}
service_user=${SERVICE_USER}
service_name=${SERVICE_NAME}
install_dir=${INSTALL_DIR}
use_service=${USE_SERVICE}
autostart=${AUTOSTART}
job_id=${JOB_ID}
started_at=${STARTED_AT}
updated_at=$(utc_now)
finished_at=$(utc_now)
before_commit=${BEFORE_COMMIT}
after_commit=${AFTER_COMMIT}
EOF
  fi

  exit 0
) >"${LOG_FILE}" 2>&1 &

emit "success" "true"
emit "code" "ok"
emit "message" "Player update started"
emit "job_id" "${JOB_ID}"
emit "repo_link" "${PLAYER_REPO_REF}"
emit "install_dir" "${INSTALL_DIR}"
emit "use_service" "${USE_SERVICE}"
emit "autostart" "${AUTOSTART}"
emit "repo_dir" ""
emit "service_user" "${SERVICE_USER}"
emit "service_name" "${SERVICE_NAME}"
emit "log_file" "${LOG_FILE}"
emit "state_file" "${STATE_FILE}"
