# Joormann-Media DevicePortal

## Overview
Local Flask portal for Raspberry Pi setup/diagnostics and panel linking.

## Config files
Defaults (overridable by env vars):
- `CONFIG_PATH`: `<PORTAL_DIR>/var/data/config.json`
- `STORAGE_CONFIG_PATH`: `<PORTAL_DIR>/var/data/config-storage.json`
- `DEVICE_PATH`: `<PORTAL_DIR>/var/data/device.json`
- `FINGERPRINT_PATH`: `<PORTAL_DIR>/var/data/fingerprint.json`
- `STATE_PATH`: `<PORTAL_DIR>/var/data/state.json`
- `PLAN_PATH`: `<PORTAL_DIR>/var/data/plan.json`
- `ASSET_DIR`: `<PORTAL_DIR>/var/assets`

## Main endpoints
- `GET /health`
- `GET /api/status`
- `GET /api/fingerprint`
- `POST /api/fingerprint/refresh`
- `POST /api/panel/test-url`
- `POST /api/panel/validate-token`
- `POST /api/panel/ping`
- `POST /api/panel/register`
- `GET /api/panel/search-users`
- `GET /api/panel/search-customers`
- `POST /api/panel/assign`
- `GET /api/panel/link-status`
- `POST /api/panel/unlink`
- `POST /api/plan/pull`
- `GET /api/plan/current`
- `GET /` local UI

## Setup wizard (Startseite)
- `Status Dashboard` Badge `SETUP` oeffnet den 3‑Step Link-Assistenten im Modal.
- Schritt 1: Panel URL pruefen inkl. Handshake-Route (`/api/device/link/handshake`).
- Schritt 2: Token verifizieren und geraet registrieren.
- Schritt 3: optionale User/Customer-Zuordnung per Live-Suche (AJAX).
- Nach erfolgreichem Abschluss wird der lokale Link-Status ohne Full-Reload aktualisiert.

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
