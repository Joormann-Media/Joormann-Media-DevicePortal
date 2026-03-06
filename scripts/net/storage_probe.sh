#!/usr/bin/env bash
set -euo pipefail

LSBLK="$(command -v lsblk || true)"
if [[ -z "${LSBLK}" ]]; then
  echo '{"devices":[],"error":"lsblk_missing"}'
  exit 0
fi

JSON_OUT="$(${LSBLK} -J -b -o NAME,KNAME,PATH,TYPE,PKNAME,HOTPLUG,RM,SIZE,FSTYPE,LABEL,UUID,PARTUUID,MOUNTPOINT,MODEL,VENDOR,SERIAL,TRAN 2>/dev/null || true)"
if [[ -z "${JSON_OUT}" ]]; then
  echo '{"devices":[]}'
  exit 0
fi

python3 - <<'PY' "${JSON_OUT}"
import json
import sys
from datetime import datetime, timezone

raw = sys.argv[1]
try:
    data = json.loads(raw)
except Exception:
    print(json.dumps({"devices": []}))
    raise SystemExit(0)

blocked_mounts = {
    "/",
    "/boot",
    "/boot/firmware",
    "/boot/efi",
    "/home",
    "/var",
}


def as_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def norm_text(value):
    if value is None:
        return ""
    text = str(value).strip()
    return text


def flatten(node, parent=None, acc=None):
    if acc is None:
        acc = []
    if not isinstance(node, dict):
        return acc
    item = dict(node)
    if parent:
        item["_parent"] = parent
    acc.append(item)
    for child in node.get("children") or []:
        flatten(child, parent=item, acc=acc)
    return acc

nodes = []
for top in (data.get("blockdevices") or []):
    flatten(top, parent=None, acc=nodes)

out = []
for item in nodes:
    dev_type = norm_text(item.get("type")).lower()
    if dev_type not in {"part", "disk"}:
        continue

    fstype = norm_text(item.get("fstype"))
    uuid = norm_text(item.get("uuid"))
    partuuid = norm_text(item.get("partuuid"))
    mountpoint = norm_text(item.get("mountpoint"))

    # Skip obvious system mounts and swap-like entries.
    if mountpoint in blocked_mounts:
        continue
    if fstype.lower() in {"swap", "squashfs"}:
        continue

    parent = item.get("_parent") or {}
    tran = norm_text(item.get("tran") or parent.get("tran")).lower()
    hotplug = as_int(item.get("hotplug", parent.get("hotplug", 0)))
    removable = as_int(item.get("rm", parent.get("rm", 0)))

    # Prefer USB/removable/hotplug media; avoid fixed internal system disks.
    is_external = tran == "usb" or hotplug == 1 or removable == 1
    if not is_external:
        continue

    # Require at least one stable identity signal.
    if not uuid and not partuuid:
        continue

    label = norm_text(item.get("label"))
    size_bytes = as_int(item.get("size", 0))
    path = norm_text(item.get("path")) or norm_text(item.get("kname"))
    if path and not path.startswith("/"):
        path = f"/dev/{path}"

    out.append(
        {
            "name": norm_text(item.get("name")),
            "kname": norm_text(item.get("kname")),
            "device_path": path,
            "type": dev_type,
            "uuid": uuid,
            "part_uuid": partuuid,
            "label": label,
            "filesystem": fstype,
            "size_bytes": size_bytes,
            "vendor": norm_text(item.get("vendor") or parent.get("vendor")),
            "model": norm_text(item.get("model") or parent.get("model")),
            "serial": norm_text(item.get("serial") or parent.get("serial")),
            "transport": tran,
            "mount_path": mountpoint,
            "mounted": bool(mountpoint),
            "hotplug": hotplug == 1,
            "removable": removable == 1,
        }
    )

print(
    json.dumps(
        {
            "detected_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "devices": out,
        },
        ensure_ascii=False,
    )
)
PY
