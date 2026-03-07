#!/usr/bin/env bash
set -euo pipefail

USERNAME="${1:-}"
if [[ -z "$USERNAME" ]]; then
  echo "missing username" >&2
  exit 2
fi

PAMTESTER="$(command -v pamtester || true)"
if [[ -z "$PAMTESTER" ]]; then
  echo "pamtester not found" >&2
  exit 127
fi

IFS= read -r PASSWORD || true
if [[ -z "${PASSWORD}" ]]; then
  echo "missing password" >&2
  exit 3
fi

if printf '%s\n' "$PASSWORD" | "$PAMTESTER" login "$USERNAME" authenticate >/dev/null 2>&1; then
  echo "auth=ok"
  exit 0
fi

echo "auth=failed" >&2
exit 10
