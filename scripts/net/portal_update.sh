#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-start}"
LEGACY_MODE="false"

if [[ "${MODE}" == "start" ]]; then
  shift || true
else
  # Backward compatibility for old callers:
  # portal_update.sh <repo_dir> <service_user> [service_name] [update_dir]
  LEGACY_MODE="true"
fi

emit() {
  local key="$1"
  local val="${2:-}"
  printf '%s=%s\n' "$key" "$val"
}

utc_now() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

read_player_update_config() {
  local cfg_path="$1"
  python3 - "$cfg_path" <<'PY'
import json
import sys
from pathlib import Path

cfg_path = Path(sys.argv[1])
cfg = {}
try:
    if cfg_path.exists():
        loaded = json.loads(cfg_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            cfg = loaded
except Exception:
    cfg = {}

def out(k, v):
    print(f"{k}={v}")

enabled = cfg.get("player_auto_update_with_portal")
if isinstance(enabled, bool):
    auto = "true" if enabled else "false"
else:
    auto = "true"

repo = str(cfg.get("player_repo_link") or cfg.get("player_repo_dir") or "").strip()
service = str(cfg.get("player_service_name") or "joormann-media-deviceplayer.service").strip() or "joormann-media-deviceplayer.service"
user = str(cfg.get("player_service_user") or "").strip()

out("auto_update", auto)
out("repo_link", repo)
out("service_name", service)
out("service_user", user)
PY
}

resolve_default_player_repo() {
  local service_user="$1"
  local portal_repo="$2"
  local home_dir
  home_dir="$(eval echo "~${service_user}")"
  local candidates=(
    "${home_dir}/Joormann-Media-DevicePlayer"
    "${home_dir}/projects/Joormann-Media-DevicePlayer"
    "$(dirname "${portal_repo}")/Joormann-Media-DevicePlayer"
  )
  local c
  for c in "${candidates[@]}"; do
    if [[ -d "${c}" ]]; then
      printf '%s' "${c}"
      return 0
    fi
  done
  printf '%s' "${home_dir}/Joormann-Media-DevicePlayer"
}

player_repo_needs_update() {
  local repo="$1"
  local user="$2"
  if [[ ! -d "${repo}/.git" ]]; then
    printf '%s' "unknown"
    return 0
  fi
  local branch
  branch="$(runuser -u "${user}" -- bash -lc "cd \"${repo}\" && git rev-parse --abbrev-ref HEAD" 2>/dev/null || true)"
  if [[ -z "${branch}" || "${branch}" == "HEAD" ]]; then
    printf '%s' "unknown"
    return 0
  fi
  runuser -u "${user}" -- bash -lc "cd \"${repo}\" && git fetch --quiet --all --prune" >/dev/null 2>&1 || {
    printf '%s' "unknown"
    return 0
  }
  local local_head upstream_head
  local_head="$(runuser -u "${user}" -- bash -lc "cd \"${repo}\" && git rev-parse HEAD" 2>/dev/null || true)"
  upstream_head="$(runuser -u "${user}" -- bash -lc "cd \"${repo}\" && git rev-parse @{u}" 2>/dev/null || true)"
  if [[ -z "${local_head}" || -z "${upstream_head}" ]]; then
    printf '%s' "unknown"
    return 0
  fi
  if [[ "${local_head}" == "${upstream_head}" ]]; then
    printf '%s' "false"
  else
    printf '%s' "true"
  fi
}

player_env_needs_reconcile() {
  local env_file="/etc/default/jm-deviceplayer"
  if [[ ! -f "${env_file}" ]]; then
    printf '%s' "true"
    return 0
  fi
  if ! grep -q '^DEVICEPLAYER_PORTAL_PLAYER_SOURCE=' "${env_file}"; then
    printf '%s' "true"
    return 0
  fi
  if ! grep -q '^DEVICEPLAYER_PORTAL_STORAGE_CONFIG=' "${env_file}"; then
    printf '%s' "true"
    return 0
  fi
  printf '%s' "false"
}

if [[ "${LEGACY_MODE}" == "true" ]]; then
  REPO_DIR="${1:-}"
  SERVICE_USER="${2:-}"
  SERVICE_NAME="${3:-device-portal.service}"
  UPDATE_DIR="${4:-/tmp/deviceportal-updates}"
else
  REPO_DIR="${1:-}"
  SERVICE_USER="${2:-}"
  SERVICE_NAME="${3:-device-portal.service}"
  UPDATE_DIR="${4:-/tmp/deviceportal-updates}"
fi

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

if [[ ! -x "${REPO_DIR}/install/setup_internal_storage.sh" ]]; then
  echo "missing installer: ${REPO_DIR}/install/setup_internal_storage.sh" >&2
  exit 4
fi

if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  echo "invalid service user: ${SERVICE_USER}" >&2
  exit 5
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
  PLAYER_UPDATE_TRIGGERED="false"
  PLAYER_UPDATE_JOB_ID=""
  PLAYER_UPDATE_REASON=""
  PLAYER_UPDATE_ERROR=""
  PLAYER_UPDATE_NEEDED="unknown"
  PLAYER_UPDATE_REPO=""
  PLAYER_UPDATE_SERVICE_NAME="joormann-media-deviceplayer.service"
  PLAYER_UPDATE_SERVICE_USER="${SERVICE_USER}"
  BEFORE_COMMIT="$(runuser -u "${SERVICE_USER}" -- bash -lc "cd \"${REPO_DIR}\" && git rev-parse --short=12 HEAD" 2>/dev/null || true)"
  if [[ -n "${BEFORE_COMMIT}" ]]; then
    echo "[git] before=${BEFORE_COMMIT}"
  fi

  RUNTIME_DATA_DIR="${REPO_DIR}/var/data"
  RUNTIME_BACKUP_DIR="${UPDATE_DIR}/${JOB_ID}-runtime-backup"
  rm -rf "${RUNTIME_BACKUP_DIR}" >/dev/null 2>&1 || true
  mkdir -p "${RUNTIME_BACKUP_DIR}" >/dev/null 2>&1 || true
  if [[ -d "${RUNTIME_DATA_DIR}" ]]; then
    find "${RUNTIME_DATA_DIR}" -maxdepth 1 -type f -name "*.json" -print0 | while IFS= read -r -d '' file; do
      base="$(basename "${file}")"
      cp -a "${file}" "${RUNTIME_BACKUP_DIR}/${base}" >/dev/null 2>&1 || true
    done
    if compgen -G "${RUNTIME_BACKUP_DIR}/*.json" > /dev/null; then
      echo "[runtime] backed up runtime json files to ${RUNTIME_BACKUP_DIR}"
    else
      echo "[runtime] no runtime json files found for backup"
    fi
  fi

  # Recover from previously interrupted pull/rebase/cherry-pick sessions.
  if runuser -u "${SERVICE_USER}" -- bash -lc "cd \"${REPO_DIR}\" && test -f .git/MERGE_HEAD"; then
    echo "[git] previous merge conflict detected, aborting merge state"
    runuser -u "${SERVICE_USER}" -- bash -lc "cd \"${REPO_DIR}\" && git merge --abort" >/dev/null 2>&1 || true
  fi
  if runuser -u "${SERVICE_USER}" -- bash -lc "cd \"${REPO_DIR}\" && test -d .git/rebase-merge -o -d .git/rebase-apply"; then
    echo "[git] previous rebase detected, aborting rebase state"
    runuser -u "${SERVICE_USER}" -- bash -lc "cd \"${REPO_DIR}\" && git rebase --abort" >/dev/null 2>&1 || true
  fi
  if runuser -u "${SERVICE_USER}" -- bash -lc "cd \"${REPO_DIR}\" && test -f .git/CHERRY_PICK_HEAD"; then
    echo "[git] previous cherry-pick detected, aborting cherry-pick state"
    runuser -u "${SERVICE_USER}" -- bash -lc "cd \"${REPO_DIR}\" && git cherry-pick --abort" >/dev/null 2>&1 || true
  fi
  # If index still contains unresolved paths (e.g. from a previous stash pop conflict),
  # reset to HEAD so ff-only pull can continue deterministically.
  UNMERGED_PATHS="$(runuser -u "${SERVICE_USER}" -- bash -lc "cd \"${REPO_DIR}\" && git diff --name-only --diff-filter=U" 2>/dev/null || true)"
  if [[ -n "${UNMERGED_PATHS}" ]]; then
    echo "[git] unresolved index conflicts detected, resetting working tree to HEAD"
    echo "${UNMERGED_PATHS}" | sed 's/^/[git] conflict: /'
    runuser -u "${SERVICE_USER}" -- bash -lc "cd \"${REPO_DIR}\" && git reset --hard HEAD" >/dev/null 2>&1 || true
    runuser -u "${SERVICE_USER}" -- bash -lc "cd \"${REPO_DIR}\" && git clean -fd" >/dev/null 2>&1 || true
  fi

  echo "[runtime] local runtime backup mode active (no git stash for var/data)"

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

  if [[ -d "${RUNTIME_BACKUP_DIR}" ]]; then
    if compgen -G "${RUNTIME_BACKUP_DIR}/*.json" > /dev/null; then
      mkdir -p "${RUNTIME_DATA_DIR}" >/dev/null 2>&1 || true
      find "${RUNTIME_BACKUP_DIR}" -maxdepth 1 -type f -name "*.json" -print0 | while IFS= read -r -d '' file; do
        base="$(basename "${file}")"
        cp -a "${file}" "${RUNTIME_DATA_DIR}/${base}" >/dev/null 2>&1 || true
        chown "${SERVICE_USER}:${SERVICE_GROUP}" "${RUNTIME_DATA_DIR}/${base}" >/dev/null 2>&1 || true
        chmod 600 "${RUNTIME_DATA_DIR}/${base}" >/dev/null 2>&1 || true
      done
      echo "[runtime] runtime json files restored from backup"
    else
      echo "[runtime] runtime backup empty, nothing to restore"
    fi
  fi

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

  echo "[storage] setup internal loop media"
  "${REPO_DIR}/install/setup_internal_storage.sh" "${SERVICE_USER}" "${SERVICE_GROUP}"
  STORAGE_RC=$?
  if [[ ${STORAGE_RC} -ne 0 ]]; then
    echo "[storage] setup failed rc=${STORAGE_RC}"
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
    PORTAL_CFG_PATH="${REPO_DIR}/var/data/config.json"
    declare -A PLAYER_CFG=()
    while IFS='=' read -r k v; do
      [[ -n "${k}" ]] || continue
      PLAYER_CFG["${k}"]="${v}"
    done < <(read_player_update_config "${PORTAL_CFG_PATH}")

    PLAYER_AUTO_UPDATE="${PLAYER_CFG[auto_update]:-true}"
    PLAYER_UPDATE_REPO="${PLAYER_CFG[repo_link]:-}"
    PLAYER_UPDATE_SERVICE_NAME="${PLAYER_CFG[service_name]:-joormann-media-deviceplayer.service}"
    PLAYER_UPDATE_SERVICE_USER="${PLAYER_CFG[service_user]:-${SERVICE_USER}}"
    if [[ -z "${PLAYER_UPDATE_REPO}" ]]; then
      PLAYER_UPDATE_REPO="$(resolve_default_player_repo "${SERVICE_USER}" "${REPO_DIR}")"
    fi
    if [[ -z "${PLAYER_UPDATE_SERVICE_USER}" ]]; then
      PLAYER_UPDATE_SERVICE_USER="${SERVICE_USER}"
    fi

    if [[ "${PLAYER_AUTO_UPDATE}" != "true" ]]; then
      PLAYER_UPDATE_REASON="auto_update_disabled"
      echo "[player] auto-update disabled in config"
    else
      PLAYER_UPDATE_NEEDED="$(player_repo_needs_update "${PLAYER_UPDATE_REPO}" "${PLAYER_UPDATE_SERVICE_USER}")"
      PLAYER_ENV_RECONCILE="$(player_env_needs_reconcile)"
      echo "[player] repo=${PLAYER_UPDATE_REPO} needed=${PLAYER_UPDATE_NEEDED} env_reconcile=${PLAYER_ENV_RECONCILE}"

      if [[ "${PLAYER_UPDATE_NEEDED}" == "true" || "${PLAYER_ENV_RECONCILE}" == "true" || "${PLAYER_UPDATE_NEEDED}" == "unknown" ]]; then
        PLAYER_UPDATE_REASON="triggered"
        PLAYER_OUT="$("${REPO_DIR}/scripts/net/player_update.sh" start "${PLAYER_UPDATE_REPO}" "${PLAYER_UPDATE_SERVICE_USER}" "${PLAYER_UPDATE_SERVICE_NAME}" "/tmp/deviceplayer-updates" "${REPO_DIR}" 2>&1)"
        PLAYER_RC=$?
        echo "${PLAYER_OUT}" | sed 's/^/[player] /'
        if [[ ${PLAYER_RC} -eq 0 ]]; then
          PLAYER_UPDATE_TRIGGERED="true"
          PLAYER_UPDATE_JOB_ID="$(printf '%s\n' "${PLAYER_OUT}" | awk -F= '/^job_id=/{print $2; exit}')"
          echo "[player] update job triggered: ${PLAYER_UPDATE_JOB_ID}"
        else
          PLAYER_UPDATE_ERROR="player_update_start_failed rc=${PLAYER_RC}"
          echo "[player] failed to trigger player update: ${PLAYER_UPDATE_ERROR}"
        fi
      else
        PLAYER_UPDATE_REASON="up_to_date"
        echo "[player] skip: repository and env already up-to-date"
      fi
    fi

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
player_update_triggered=${PLAYER_UPDATE_TRIGGERED}
player_update_job_id=${PLAYER_UPDATE_JOB_ID}
player_update_reason=${PLAYER_UPDATE_REASON}
player_update_needed=${PLAYER_UPDATE_NEEDED}
player_update_repo=${PLAYER_UPDATE_REPO}
player_update_service_name=${PLAYER_UPDATE_SERVICE_NAME}
player_update_service_user=${PLAYER_UPDATE_SERVICE_USER}
player_update_error=${PLAYER_UPDATE_ERROR}
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
player_update_triggered=false
player_update_job_id=
player_update_reason=portal_restart_failed
player_update_needed=unknown
player_update_repo=
player_update_service_name=
player_update_service_user=
player_update_error=
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
