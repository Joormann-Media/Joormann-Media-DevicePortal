# Joormann-Media DevicePortal

## Overview
Local Flask portal for Raspberry Pi setup/diagnostics and panel linking.

## Config files
Defaults (overridable by env vars):
- `CONFIG_PATH`: `/etc/device/config.json`
- `DEVICE_PATH`: `/etc/device/device.json`
- `FINGERPRINT_PATH`: `/etc/device/fingerprint.json`
- `STATE_PATH`: `/etc/device/state.json`
- `PLAN_PATH`: `/etc/device/plan.json`
- `ASSET_DIR`: `<PORTAL_DIR>/var/assets`

## Main endpoints
- `GET /health`
- `GET /api/status`
- `GET /api/fingerprint`
- `POST /api/fingerprint/refresh`
- `POST /api/panel/test-url`
- `POST /api/panel/ping`
- `POST /api/panel/register`
- `GET /api/panel/link-status`
- `POST /api/panel/unlink`
- `POST /api/plan/pull`
- `GET /api/plan/current`
- `GET /` local UI

## State contract (`STATE_PATH`)
Contains mode/setup/play, panel status, hostname/ip, selected stream/device slug and timestamp.

## Plan contract (`PLAN_PATH`)
Contains panel playback plan payload plus metadata (`saved_at`, `device_slug`, `stream_slug`).

## Run (dev)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
./scripts/dev_run.sh
```
