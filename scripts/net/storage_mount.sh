#!/usr/bin/env bash
set -euo pipefail

SELECTOR_TYPE="${1:-}"
SELECTOR_VALUE="${2:-}"
MOUNT_PATH="${3:-}"
MOUNT_OPTIONS="${4:-defaults,noatime,nofail}"

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

if mount -o "${MOUNT_OPTIONS}" "${DEV}" "${MOUNT_PATH}"; then
  SRC="$(findmnt -rn -M "${MOUNT_PATH}" -o SOURCE 2>/dev/null || true)"
  echo "success=true"
  echo "mounted=true"
  echo "device=${SRC}"
  echo "mount_path=${MOUNT_PATH}"
  echo "selector_type=${SELECTOR_TYPE}"
  echo "selector_value=${SELECTOR_VALUE}"
  exit 0
fi

echo "mount failed" >&2
exit 5
