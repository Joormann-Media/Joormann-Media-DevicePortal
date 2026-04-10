#!/usr/bin/env bash
set -euo pipefail

OUT="${1:-}"
if [[ -z "${OUT}" ]]; then
  echo "missing output path" >&2
  exit 2
fi

mkdir -p "$(dirname "$OUT")"

if ! command -v loginctl >/dev/null 2>&1; then
  echo "loginctl not available" >&2
  exit 3
fi

pick_active_session() {
  local sid
  while read -r sid _; do
    [[ -n "$sid" ]] || continue
    local active type user display remote
    active="$(loginctl show-session "$sid" -p Active --value 2>/dev/null || echo "")"
    [[ "$active" == "yes" ]] || continue
    remote="$(loginctl show-session "$sid" -p Remote --value 2>/dev/null || echo "")"
    [[ "$remote" == "no" || -z "$remote" ]] || continue
    type="$(loginctl show-session "$sid" -p Type --value 2>/dev/null || echo "")"
    user="$(loginctl show-session "$sid" -p Name --value 2>/dev/null || echo "")"
    display="$(loginctl show-session "$sid" -p Display --value 2>/dev/null || echo "")"
    if [[ "$type" == "wayland" || "$type" == "x11" ]]; then
      echo "$sid|$type|$user|$display"
      return 0
    fi
  done < <(loginctl list-sessions --no-legend 2>/dev/null | awk '{print $1, $2}')
  return 1
}

session_info="$(pick_active_session || true)"
if [[ -z "$session_info" ]]; then
  echo "no_active_session" >&2
  exit 4
fi

IFS="|" read -r SESSION_ID SESSION_TYPE SESSION_USER SESSION_DISPLAY <<<"$session_info"
if [[ -z "$SESSION_USER" ]]; then
  echo "session_user_missing" >&2
  exit 5
fi

USER_HOME="$(getent passwd "$SESSION_USER" | cut -d: -f6)"
USER_UID="$(id -u "$SESSION_USER")"
USER_RUNTIME="/run/user/${USER_UID}"

ENV_VARS=(
  "XDG_RUNTIME_DIR=${USER_RUNTIME}"
  "DBUS_SESSION_BUS_ADDRESS=unix:path=${USER_RUNTIME}/bus"
)

if [[ "$SESSION_TYPE" == "wayland" ]]; then
  if [[ -S "${USER_RUNTIME}/wayland-0" ]]; then
    ENV_VARS+=("WAYLAND_DISPLAY=wayland-0")
  fi
  if command -v gnome-screenshot >/dev/null 2>&1; then
    runuser -u "$SESSION_USER" -- env "${ENV_VARS[@]}" gnome-screenshot -f "$OUT" && exit 0
  fi
  if command -v grim >/dev/null 2>&1; then
    runuser -u "$SESSION_USER" -- env "${ENV_VARS[@]}" grim "$OUT" && exit 0
  fi
fi

DISPLAY_VAL="${SESSION_DISPLAY:-:0}"
ENV_VARS+=("DISPLAY=${DISPLAY_VAL}")
if [[ -n "$USER_HOME" && -f "${USER_HOME}/.Xauthority" ]]; then
  ENV_VARS+=("XAUTHORITY=${USER_HOME}/.Xauthority")
fi

if command -v import >/dev/null 2>&1; then
  runuser -u "$SESSION_USER" -- env "${ENV_VARS[@]}" import -window root "$OUT" && exit 0
fi
if command -v scrot >/dev/null 2>&1; then
  runuser -u "$SESSION_USER" -- env "${ENV_VARS[@]}" scrot -o "$OUT" && exit 0
fi
if command -v xwd >/dev/null 2>&1 && command -v convert >/dev/null 2>&1; then
  runuser -u "$SESSION_USER" -- env "${ENV_VARS[@]}" bash -lc "xwd -root -silent | convert xwd:- '$OUT'" && exit 0
fi

echo "capture_failed" >&2
exit 6
