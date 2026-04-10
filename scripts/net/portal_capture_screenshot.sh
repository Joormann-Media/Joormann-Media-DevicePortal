#!/usr/bin/env bash
set -euo pipefail

OUT="${1:-}"
if [[ -z "${OUT}" ]]; then
  echo "missing output path" >&2
  exit 2
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg not installed" >&2
  exit 3
fi

mkdir -p "$(dirname "$OUT")"

try_kmsgrab() {
  local card="$1"
  ffmpeg -y -loglevel error -f kmsgrab -device "$card" -i - -frames:v 1 -vf hwdownload,format=bgra "$OUT" && return 0
  return 1
}

try_fbdev() {
  local fb="$1"
  ffmpeg -y -loglevel error -f fbdev -i "$fb" -frames:v 1 "$OUT" && return 0
  return 1
}

for card in /dev/dri/card*; do
  [[ -e "$card" ]] || continue
  if try_kmsgrab "$card"; then
    exit 0
  fi
done

if [[ -e /dev/fb0 ]]; then
  if try_fbdev /dev/fb0; then
    exit 0
  fi
fi

echo "capture_failed" >&2
exit 4
