#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-status}"
shift || true

BTCTL="$(command -v bluetoothctl || true)"
if [[ -z "${BTCTL}" ]]; then
  echo "bluetoothctl not found" >&2
  exit 127
fi

normalize_bool() {
  local raw="${1:-}"
  raw="$(echo "${raw}" | tr '[:upper:]' '[:lower:]' | xargs)"
  case "${raw}" in
    yes|true|on|1|enabled) echo "1" ;;
    no|false|off|0|disabled) echo "0" ;;
    *) echo "" ;;
  esac
}

normalize_timeout() {
  local raw="${1:-}"
  raw="$(echo "${raw}" | xargs)"
  if [[ "${raw}" =~ ^0[xX][0-9a-fA-F]+$ ]]; then
    printf '%d\n' "$((raw))"
    return 0
  fi
  if [[ "${raw}" =~ ^[0-9]+$ ]]; then
    printf '%d\n' "${raw}"
    return 0
  fi
  echo ""
}

read_status() {
  local show_out powered_raw discoverable_raw pairable_raw d_to_raw p_to_raw

  if ! show_out="$("${BTCTL}" show 2>/dev/null)"; then
    echo "failed to read bluetooth controller state" >&2
    exit 1
  fi
  if [[ -z "${show_out}" ]]; then
    echo "no bluetooth controller data" >&2
    exit 1
  fi

  powered_raw="$(echo "${show_out}" | awk -F': ' '/^[[:space:]]*Powered:/{print $2; exit}')"
  discoverable_raw="$(echo "${show_out}" | awk -F': ' '/^[[:space:]]*Discoverable:/{print $2; exit}')"
  pairable_raw="$(echo "${show_out}" | awk -F': ' '/^[[:space:]]*Pairable:/{print $2; exit}')"
  d_to_raw="$(echo "${show_out}" | awk -F': ' '/^[[:space:]]*DiscoverableTimeout:/{print $2; exit}')"
  p_to_raw="$(echo "${show_out}" | awk -F': ' '/^[[:space:]]*PairableTimeout:/{print $2; exit}')"

  echo "powered=$(normalize_bool "${powered_raw}")"
  echo "discoverable=$(normalize_bool "${discoverable_raw}")"
  echo "pairable=$(normalize_bool "${pairable_raw}")"
  echo "discoverable_timeout=$(normalize_timeout "${d_to_raw}")"
  echo "pairable_timeout=$(normalize_timeout "${p_to_raw}")"
}

run_config() {
  local discoverable_state="${1:-keep}"
  local discoverable_timeout="${2:-keep}"
  local pairable_state="${3:-keep}"
  local pairable_timeout="${4:-keep}"
  local needs_power=0

  case "${discoverable_state}" in
    keep|on|off) ;;
    *) echo "invalid discoverable state" >&2; exit 2 ;;
  esac
  case "${pairable_state}" in
    keep|on|off) ;;
    *) echo "invalid pairable state" >&2; exit 2 ;;
  esac
  if [[ "${discoverable_timeout}" != "keep" && ! "${discoverable_timeout}" =~ ^[0-9]+$ ]]; then
    echo "invalid discoverable timeout" >&2
    exit 2
  fi
  if [[ "${pairable_timeout}" != "keep" && ! "${pairable_timeout}" =~ ^[0-9]+$ ]]; then
    echo "invalid pairable timeout" >&2
    exit 2
  fi

  if [[ "${discoverable_state}" == "on" || "${pairable_state}" == "on" ]]; then
    needs_power=1
  fi

  {
    # Keep an active agent during pairing, otherwise many phones fail with
    # "pairing not accepted" because no local confirmation handler exists.
    echo "agent KeyboardDisplay"
    echo "default-agent"
    if [[ "${needs_power}" -eq 1 ]]; then
      echo "power on"
    fi
    if [[ "${discoverable_timeout}" != "keep" ]]; then
      echo "discoverable-timeout ${discoverable_timeout}"
    fi
    if [[ "${discoverable_state}" != "keep" ]]; then
      echo "discoverable ${discoverable_state}"
    fi
    if [[ "${pairable_timeout}" != "keep" ]]; then
      echo "pairable-timeout ${pairable_timeout}"
    fi
    if [[ "${pairable_state}" != "keep" ]]; then
      echo "pairable ${pairable_state}"
    fi
    echo "quit"
  } | "${BTCTL}" >/dev/null 2>&1 || {
    echo "failed to apply bluetooth settings" >&2
    exit 1
  }
}

case "${MODE}" in
  status)
    read_status
    ;;
  config)
    run_config "${1:-keep}" "${2:-keep}" "${3:-keep}" "${4:-keep}"
    read_status
    ;;
  *)
    echo "usage: $0 {status|config [discoverable(on|off|keep)] [discoverable_timeout|keep] [pairable(on|off|keep)] [pairable_timeout|keep]}" >&2
    exit 2
    ;;
esac
