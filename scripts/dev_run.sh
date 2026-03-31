#!/usr/bin/env bash
set -euo pipefail

export FLASK_APP=app.main:app
PORTAL_PORT="${PORTAL_PORT:-5070}"
exec flask run --host 0.0.0.0 --port "${PORTAL_PORT}"
