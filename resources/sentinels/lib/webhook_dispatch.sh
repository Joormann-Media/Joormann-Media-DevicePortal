#!/usr/bin/env bash
set -euo pipefail

WEBHOOK_MODE="${WEBHOOK_MODE:-discord}"
DISCORD_WEBHOOK="${DISCORD_WEBHOOK:-}"
DISCORD_WEBHOOK_PRIMARY="${DISCORD_WEBHOOK_PRIMARY:-}"
INTERNAL_WEBHOOK_URL="${INTERNAL_WEBHOOK_URL:-}"
INTERNAL_WEBHOOK_SECRET="${INTERNAL_WEBHOOK_SECRET:-}"

payload="${1:-}"
if [ -z "${payload}" ]; then
  payload="$(cat)"
fi

if [ -z "${payload}" ]; then
  exit 0
fi

send_to() {
  local target="$1"
  [ -n "${target}" ] || return 0
  curl -fsS -H "Content-Type: application/json" -X POST -d "${payload}" "${target}" >/dev/null 2>&1 || true
}

append_secret() {
  local target="$1"
  local secret="$2"
  [ -n "${target}" ] || { echo ""; return 0; }
  [ -n "${secret}" ] || { echo "${target}"; return 0; }
  local sep='?'
  case "${target}" in *\?*) sep='&';; esac
  echo "${target}${sep}secret=${secret}"
}

case "${WEBHOOK_MODE}" in
  internal)
    send_to "$(append_secret "${INTERNAL_WEBHOOK_URL}" "${INTERNAL_WEBHOOK_SECRET}")"
    ;;
  both)
    send_to "${DISCORD_WEBHOOK_PRIMARY:-${DISCORD_WEBHOOK}}"
    send_to "$(append_secret "${INTERNAL_WEBHOOK_URL}" "${INTERNAL_WEBHOOK_SECRET}")"
    ;;
  *)
    send_to "${DISCORD_WEBHOOK_PRIMARY:-${DISCORD_WEBHOOK}}"
    ;;
esac

