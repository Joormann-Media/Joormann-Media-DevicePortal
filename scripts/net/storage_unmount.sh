#!/usr/bin/env bash
set -euo pipefail

MOUNT_PATH="${1:-}"

if [[ -z "${MOUNT_PATH}" ]]; then
  echo "usage: $0 <mount_path>" >&2
  exit 2
fi

if [[ ! "${MOUNT_PATH}" =~ ^/mnt/deviceportal/storage/[A-Za-z0-9._-]+$ ]]; then
  echo "invalid mount path" >&2
  exit 3
fi

if ! findmnt -rn --target "${MOUNT_PATH}" >/dev/null 2>&1; then
  echo "success=true"
  echo "mounted=false"
  echo "mount_path=${MOUNT_PATH}"
  exit 0
fi

if umount "${MOUNT_PATH}"; then
  echo "success=true"
  echo "mounted=false"
  echo "mount_path=${MOUNT_PATH}"
  exit 0
fi

echo "umount failed" >&2
exit 5
