#!/usr/bin/env bash
set -euo pipefail

MEDIA_IMG="${MEDIA_IMG:-/var/lib/deviceportal/media.img}"
MEDIA_MOUNT="${MEDIA_MOUNT:-/mnt/deviceportal/media}"
MEDIA_FS="ext4"
MEDIA_SIZE_GB="${MEDIA_SIZE_GB:-20}"
RESERVE_GB="${MEDIA_RESERVE_GB:-3}"
SERVICE_USER="${1:-www-data}"
SERVICE_GROUP="${2:-$SERVICE_USER}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

if ! [[ "${MEDIA_SIZE_GB}" =~ ^[0-9]+$ ]]; then
  echo "Invalid MEDIA_SIZE_GB: ${MEDIA_SIZE_GB}" >&2
  exit 2
fi
if ! [[ "${RESERVE_GB}" =~ ^[0-9]+$ ]]; then
  echo "Invalid MEDIA_RESERVE_GB: ${RESERVE_GB}" >&2
  exit 2
fi

SIZE_BYTES=$(( MEDIA_SIZE_GB * 1024 * 1024 * 1024 ))
RESERVE_BYTES=$(( RESERVE_GB * 1024 * 1024 * 1024 ))

install -d -m 0755 /var/lib/deviceportal
install -d -m 0775 "${MEDIA_MOUNT}"
install -d -m 0775 /mnt/deviceportal/storage

if [[ ! -e "${MEDIA_IMG}" ]]; then
  AVAILABLE_BYTES="$(df -PB1 /var/lib | awk 'NR==2 {print $4}')"
  REQUIRED_BYTES=$(( SIZE_BYTES + RESERVE_BYTES ))
  if [[ -z "${AVAILABLE_BYTES}" || "${AVAILABLE_BYTES}" -lt "${REQUIRED_BYTES}" ]]; then
    echo "WARN: insufficient free space for ${MEDIA_SIZE_GB}G media.img (+${RESERVE_GB}G reserve)." >&2
    echo "WARN: skipping internal media loop setup." >&2
    exit 0
  fi

  if command -v fallocate >/dev/null 2>&1; then
    fallocate -l "${MEDIA_SIZE_GB}G" "${MEDIA_IMG}"
  else
    truncate -s "${MEDIA_SIZE_GB}G" "${MEDIA_IMG}"
  fi
  chmod 0640 "${MEDIA_IMG}"
  echo "Created ${MEDIA_IMG} (${MEDIA_SIZE_GB}G)"
fi

FS_TYPE="$(blkid -o value -s TYPE "${MEDIA_IMG}" 2>/dev/null || true)"
if [[ -z "${FS_TYPE}" ]]; then
  mkfs.ext4 -F "${MEDIA_IMG}" >/dev/null
  FS_TYPE="ext4"
  echo "Formatted ${MEDIA_IMG} as ext4"
fi

if [[ "${FS_TYPE}" != "${MEDIA_FS}" ]]; then
  echo "WARN: ${MEDIA_IMG} filesystem is '${FS_TYPE}', expected '${MEDIA_FS}'. Skipping mount/fstab changes." >&2
  exit 0
fi

FSTAB_LINE="${MEDIA_IMG} ${MEDIA_MOUNT} ${MEDIA_FS} loop,nofail 0 0"
if ! awk -v src="${MEDIA_IMG}" -v mnt="${MEDIA_MOUNT}" '($1==src && $2==mnt){found=1} END{exit !found}' /etc/fstab; then
  printf '%s\n' "${FSTAB_LINE}" >> /etc/fstab
  echo "Added fstab entry for internal media loop"
fi

if ! findmnt -rn --target "${MEDIA_MOUNT}" >/dev/null 2>&1; then
  if ! mount "${MEDIA_MOUNT}"; then
    mount -t ext4 -o loop,nofail "${MEDIA_IMG}" "${MEDIA_MOUNT}"
  fi
fi

chown "${SERVICE_USER}:${SERVICE_GROUP}" "${MEDIA_MOUNT}" || true
chmod 0775 "${MEDIA_MOUNT}" || true
chown "${SERVICE_USER}:${SERVICE_GROUP}" /mnt/deviceportal/storage || true
chmod 0775 /mnt/deviceportal/storage || true

echo "Internal media loop ready: ${MEDIA_IMG} -> ${MEDIA_MOUNT}"
