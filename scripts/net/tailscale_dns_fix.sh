#!/usr/bin/env bash
set -euo pipefail

NMCLI="$(command -v nmcli || true)"
TAILSCALE="$(command -v tailscale || true)"

if [[ -z "${TAILSCALE}" ]]; then
  echo "success=false"
  echo "code=tailscale_missing"
  echo "message=Tailscale command not found"
  exit 127
fi

if [[ -z "${NMCLI}" ]]; then
  echo "success=false"
  echo "code=nmcli_missing"
  echo "message=nmcli command not found"
  exit 127
fi

set +e
TS_OUT="$("${TAILSCALE}" set --accept-dns=false 2>&1)"
TS_RC=$?
set -e
if [[ ${TS_RC} -ne 0 ]]; then
  TS_OUT="${TS_OUT//$'\n'/ }"
  echo "success=false"
  echo "code=tailscale_config_failed"
  echo "message=Failed to disable Tailscale DNS takeover"
  echo "details=${TS_OUT}"
  echo "hint=This Tailscale version may not support 'tailscale set'. Avoid automatic 'tailscale up' because it can reset runtime flags."
  exit 2
fi

ACTIVE_CONN="$("${NMCLI}" -t -f NAME,TYPE,DEVICE connection show --active 2>/dev/null | awk -F: '$2=="802-3-ethernet" && $3!="" {print $1; exit}')"
if [[ -z "${ACTIVE_CONN}" ]]; then
  ACTIVE_CONN="$("${NMCLI}" -t -f NAME,TYPE,DEVICE connection show --active 2>/dev/null | awk -F: '$2=="802-11-wireless" && $3!="" {print $1; exit}')"
fi
if [[ -z "${ACTIVE_CONN}" ]]; then
  ACTIVE_CONN="$("${NMCLI}" -t -f NAME connection show --active 2>/dev/null | head -n1)"
fi
if [[ -n "${ACTIVE_CONN}" ]]; then
  "${NMCLI}" connection up "${ACTIVE_CONN}" >/dev/null 2>&1 || true
fi

DNS_SERVERS="$(awk '/^nameserver / {print $2}' /etc/resolv.conf 2>/dev/null | paste -sd, - || true)"
SEARCH_DOMAINS="$(awk '/^search / {for(i=2;i<=NF;i++) printf "%s%s", $i, (i==NF?"":",")}' /etc/resolv.conf 2>/dev/null || true)"

echo "success=true"
echo "code=ok"
echo "message=Tailscale DNS takeover disabled"
echo "connection=${ACTIVE_CONN}"
echo "dns=${DNS_SERVERS}"
echo "search=${SEARCH_DOMAINS}"
