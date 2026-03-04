#!/usr/bin/env bash
set -euo pipefail

export FLASK_APP=app.main:app
exec flask run --host 0.0.0.0 --port 5070
