#!/usr/bin/env bash
set -euo pipefail

MEDIA_IMG="${MEDIA_IMG:-/var/lib/deviceportal/media.img}"
MEDIA_MOUNT="${MEDIA_MOUNT:-/mnt/deviceportal/media}"
MEDIA_FS="${MEDIA_FS:-ext4}"
MOUNT_OPTIONS="${MOUNT_OPTIONS:-loop,nofail}"
SERVICE_USER="${SERVICE_USER:-${SUDO_USER:-www-data}}"
SERVICE_GROUP="${SERVICE_GROUP:-${SERVICE_USER}}"

if [[ ! -f "${MEDIA_IMG}" ]]; then
  echo "internal image missing: ${MEDIA_IMG}" >&2
  exit 4
fi

if [[ ! -d "${MEDIA_MOUNT}" ]]; then
  mkdir -p "${MEDIA_MOUNT}"
fi

if findmnt -rn -M "${MEDIA_MOUNT}" >/dev/null 2>&1; then
  SRC="$(findmnt -rn -M "${MEDIA_MOUNT}" -o SOURCE 2>/dev/null || true)"
  FSTYPE="$(findmnt -rn -M "${MEDIA_MOUNT}" -o FSTYPE 2>/dev/null || true)"
  echo "success=true"
  echo "mounted=true"
  echo "mount_path=${MEDIA_MOUNT}"
  echo "device=${SRC}"
  echo "filesystem=${FSTYPE}"
  exit 0
fi

if ! mount -t "${MEDIA_FS}" -o "${MOUNT_OPTIONS}" "${MEDIA_IMG}" "${MEDIA_MOUNT}"; then
  echo "failed to mount internal media loop" >&2
  exit 5
fi

chown "${SERVICE_USER}:${SERVICE_GROUP}" "${MEDIA_MOUNT}" 2>/dev/null || true
chmod 0775 "${MEDIA_MOUNT}" 2>/dev/null || true

SRC="$(findmnt -rn -M "${MEDIA_MOUNT}" -o SOURCE 2>/dev/null || true)"
FSTYPE="$(findmnt -rn -M "${MEDIA_MOUNT}" -o FSTYPE 2>/dev/null || true)"
echo "success=true"
echo "mounted=true"
echo "mount_path=${MEDIA_MOUNT}"
echo "device=${SRC}"
echo "filesystem=${FSTYPE}"
