#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-}"
TARGET_MAC="${2:-}"

BTCTL="$(command -v bluetoothctl || true)"
if [[ -z "${BTCTL}" ]]; then
  echo "bluetoothctl not found" >&2
  exit 127
fi

TIMEOUT_BIN="$(command -v timeout || true)"

if [[ -z "${ACTION}" ]]; then
  echo "missing action (confirm|reject)" >&2
  exit 2
fi
if [[ -z "${TARGET_MAC}" ]]; then
  echo "missing target mac" >&2
  exit 2
fi
if [[ ! "${TARGET_MAC}" =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]]; then
  echo "invalid mac address" >&2
  exit 2
fi

run_btctl() {
  if [[ -n "${TIMEOUT_BIN}" ]]; then
    "${TIMEOUT_BIN}" 12s "${BTCTL}"
  else
    "${BTCTL}"
  fi
}

collect_info() {
  "${BTCTL}" info "${TARGET_MAC}" 2>/dev/null || true
}

case "${ACTION}" in
  confirm)
    {
      echo "power on"
      echo "trust ${TARGET_MAC}"
      echo "pair ${TARGET_MAC}"
      echo "connect ${TARGET_MAC}"
      echo "info ${TARGET_MAC}"
      echo "quit"
    } | run_btctl >/tmp/bt_pairing_action.out 2>/tmp/bt_pairing_action.err || true
    ;;
  reject)
    {
      echo "disconnect ${TARGET_MAC}"
      echo "remove ${TARGET_MAC}"
      echo "quit"
    } | run_btctl >/tmp/bt_pairing_action.out 2>/tmp/bt_pairing_action.err || true
    ;;
  *)
    echo "invalid action: ${ACTION}" >&2
    exit 2
    ;;
esac

info_out="$(collect_info)"
paired="$(echo "${info_out}" | awk -F': ' '/^[[:space:]]*Paired:/{print $2; exit}')"
trusted="$(echo "${info_out}" | awk -F': ' '/^[[:space:]]*Trusted:/{print $2; exit}')"
connected="$(echo "${info_out}" | awk -F': ' '/^[[:space:]]*Connected:/{print $2; exit}')"
name="$(echo "${info_out}" | awk -F': ' '/^[[:space:]]*Name:/{print $2; exit}')"

stdout_tail="$(tail -n 2 /tmp/bt_pairing_action.out 2>/dev/null | xargs || true)"
stderr_tail="$(tail -n 2 /tmp/bt_pairing_action.err 2>/dev/null | xargs || true)"

echo "action=${ACTION}"
echo "mac=${TARGET_MAC}"
echo "name=${name}"
echo "paired=${paired}"
echo "trusted=${trusted}"
echo "connected=${connected}"
echo "stdout_tail=${stdout_tail}"
echo "stderr_tail=${stderr_tail}"

rm -f /tmp/bt_pairing_action.out /tmp/bt_pairing_action.err

