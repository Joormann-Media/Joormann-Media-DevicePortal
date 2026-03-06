#!/usr/bin/env bash
set -euo pipefail

SELECTOR_TYPE="${1:-}"
SELECTOR_VALUE="${2:-}"
MOUNT_PATH="${3:-}"
MOUNT_OPTIONS="${4:-defaults,noatime,nofail}"
SERVICE_USER="${SERVICE_USER:-${SUDO_USER:-www-data}}"
SERVICE_GROUP="${SERVICE_GROUP:-${SERVICE_USER}}"

if [[ -z "${SELECTOR_TYPE}" || -z "${SELECTOR_VALUE}" || -z "${MOUNT_PATH}" ]]; then
  echo "usage: $0 <uuid|partuuid> <value> <mount_path> [mount_options]" >&2
  exit 2
fi

if [[ "${SELECTOR_TYPE}" != "uuid" && "${SELECTOR_TYPE}" != "partuuid" ]]; then
  echo "invalid selector type: ${SELECTOR_TYPE}" >&2
  exit 3
fi

if [[ ! "${SELECTOR_VALUE}" =~ ^[A-Za-z0-9._:-]+$ ]]; then
  echo "invalid selector value" >&2
  exit 3
fi

if [[ ! "${MOUNT_PATH}" =~ ^/mnt/deviceportal/storage/[A-Za-z0-9._-]+$ ]]; then
  echo "invalid mount path" >&2
  exit 3
fi

if [[ "${SELECTOR_TYPE}" == "uuid" ]]; then
  DEV="/dev/disk/by-uuid/${SELECTOR_VALUE}"
else
  DEV="/dev/disk/by-partuuid/${SELECTOR_VALUE}"
fi

if [[ ! -e "${DEV}" ]]; then
  echo "device not found: ${DEV}" >&2
  exit 4
fi

mkdir -p "${MOUNT_PATH}"

if findmnt -rn -M "${MOUNT_PATH}" >/dev/null 2>&1; then
  SRC="$(findmnt -rn -M "${MOUNT_PATH}" -o SOURCE 2>/dev/null || true)"
  echo "success=true"
  echo "mounted=true"
  echo "device=${SRC}"
  echo "mount_path=${MOUNT_PATH}"
  exit 0
fi

if id -u "${SERVICE_USER}" >/dev/null 2>&1; then
  SERVICE_UID="$(id -u "${SERVICE_USER}")"
else
  SERVICE_UID="$(id -u www-data 2>/dev/null || echo 33)"
fi
if getent group "${SERVICE_GROUP}" >/dev/null 2>&1; then
  SERVICE_GID="$(getent group "${SERVICE_GROUP}" | cut -d: -f3)"
else
  SERVICE_GID="$(id -g "${SERVICE_USER}" 2>/dev/null || id -g www-data 2>/dev/null || echo 33)"
fi

FSTYPE="$(blkid -o value -s TYPE "${DEV}" 2>/dev/null || true)"
FINAL_MOUNT_OPTIONS="${MOUNT_OPTIONS}"
case "${FSTYPE}" in
  vfat|fat|msdos|exfat)
    if [[ "${FINAL_MOUNT_OPTIONS}" != *"uid="* ]]; then
      FINAL_MOUNT_OPTIONS="${FINAL_MOUNT_OPTIONS},uid=${SERVICE_UID}"
    fi
    if [[ "${FINAL_MOUNT_OPTIONS}" != *"gid="* ]]; then
      FINAL_MOUNT_OPTIONS="${FINAL_MOUNT_OPTIONS},gid=${SERVICE_GID}"
    fi
    if [[ "${FINAL_MOUNT_OPTIONS}" != *"umask="* ]]; then
      FINAL_MOUNT_OPTIONS="${FINAL_MOUNT_OPTIONS},umask=002"
    fi
    ;;
esac

if mount -o "${FINAL_MOUNT_OPTIONS}" "${DEV}" "${MOUNT_PATH}"; then
  if [[ "${FSTYPE}" != "vfat" && "${FSTYPE}" != "fat" && "${FSTYPE}" != "msdos" && "${FSTYPE}" != "exfat" ]]; then
    chown "${SERVICE_USER}:${SERVICE_GROUP}" "${MOUNT_PATH}" 2>/dev/null || true
    chmod 0775 "${MOUNT_PATH}" 2>/dev/null || true
  fi
  SRC="$(findmnt -rn -M "${MOUNT_PATH}" -o SOURCE 2>/dev/null || true)"
  echo "success=true"
  echo "mounted=true"
  echo "device=${SRC}"
  echo "mount_path=${MOUNT_PATH}"
  echo "selector_type=${SELECTOR_TYPE}"
  echo "selector_value=${SELECTOR_VALUE}"
  echo "filesystem=${FSTYPE}"
  echo "mount_options=${FINAL_MOUNT_OPTIONS}"
  exit 0
fi

echo "mount failed" >&2
exit 5
