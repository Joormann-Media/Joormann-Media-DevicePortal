#!/usr/bin/env bash
set -euo pipefail

SELECTOR_TYPE="${1:-}"
SELECTOR_VALUE="${2:-}"
FILESYSTEM="${3:-vfat}"
LABEL_RAW="${4:-}"

if [[ -z "${SELECTOR_TYPE}" || -z "${SELECTOR_VALUE}" ]]; then
  echo "usage: $0 <uuid|partuuid> <value> [filesystem] [label]" >&2
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

FILESYSTEM="$(echo "${FILESYSTEM}" | tr '[:upper:]' '[:lower:]')"
case "${FILESYSTEM}" in
  ext4|vfat|exfat) ;;
  *)
    echo "unsupported filesystem: ${FILESYSTEM}" >&2
    exit 3
    ;;
esac

LABEL="$(echo "${LABEL_RAW}" | tr -cd 'A-Za-z0-9 _.-' | sed 's/[[:space:]]\+/ /g' | sed 's/^ //; s/ $//')"

if [[ "${SELECTOR_TYPE}" == "uuid" ]]; then
  DEV_LINK="/dev/disk/by-uuid/${SELECTOR_VALUE}"
else
  DEV_LINK="/dev/disk/by-partuuid/${SELECTOR_VALUE}"
fi

if [[ ! -e "${DEV_LINK}" ]]; then
  echo "device not found: ${DEV_LINK}" >&2
  exit 4
fi

DEV_REAL="$(readlink -f "${DEV_LINK}" 2>/dev/null || true)"
if [[ -z "${DEV_REAL}" || ! -b "${DEV_REAL}" ]]; then
  echo "resolved device is not a block device: ${DEV_REAL:-<empty>}" >&2
  exit 4
fi

MOUNT_TARGET="$(findmnt -rn -S "${DEV_REAL}" -o TARGET 2>/dev/null | head -n1 || true)"
if [[ -n "${MOUNT_TARGET}" ]]; then
  umount "${MOUNT_TARGET}"
fi

case "${FILESYSTEM}" in
  ext4)
    if [[ -n "${LABEL}" ]]; then
      mkfs.ext4 -F -L "${LABEL:0:16}" "${DEV_REAL}" >/dev/null
    else
      mkfs.ext4 -F "${DEV_REAL}" >/dev/null
    fi
    ;;
  vfat)
    if [[ -n "${LABEL}" ]]; then
      mkfs.vfat -F 32 -n "${LABEL:0:11}" "${DEV_REAL}" >/dev/null
    else
      mkfs.vfat -F 32 "${DEV_REAL}" >/dev/null
    fi
    ;;
  exfat)
    if ! command -v mkfs.exfat >/dev/null 2>&1; then
      echo "mkfs.exfat not available" >&2
      exit 6
    fi
    if [[ -n "${LABEL}" ]]; then
      mkfs.exfat -n "${LABEL:0:15}" "${DEV_REAL}" >/dev/null
    else
      mkfs.exfat "${DEV_REAL}" >/dev/null
    fi
    ;;
esac

sync

NEW_UUID="$(blkid -o value -s UUID "${DEV_REAL}" 2>/dev/null || true)"
NEW_PARTUUID="$(blkid -o value -s PARTUUID "${DEV_REAL}" 2>/dev/null || true)"
NEW_LABEL="$(blkid -o value -s LABEL "${DEV_REAL}" 2>/dev/null || true)"

echo "success=true"
echo "formatted=true"
echo "device=${DEV_REAL}"
echo "filesystem=${FILESYSTEM}"
echo "label=${NEW_LABEL:-${LABEL}}"
echo "uuid=${NEW_UUID}"
echo "partuuid=${NEW_PARTUUID}"
