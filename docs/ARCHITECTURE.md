# Architecture Audit: Joormann-Media DevicePortal

## Abstract
Dieses Dokument beschreibt die aktuell implementierte Architektur des Repos `/home/djanebmb/projects/Joormann-Media-Deviceportal`, inklusive Startpfaden, Modulen, Dateiverträgen und Laufzeitverhalten.

## 1) Repo-Struktur

```text
app/
  __init__.py           # App-Factory + Blueprint-Registrierung
  main.py               # Runtime Entry-Point (Flask app)
  api/
    routes_status.py    # Health/Status/Fingerprint/State
    routes_panel.py     # Panel URL/Ping/Register/Link-Status/Unlink
    routes_plan.py      # Plan Pull + Plan Current
  core/
    paths.py            # ENV-overridable Datei-Pfade
    config.py           # Default-Config + Normalisierung
    device.py           # Device Identity (UUID/Auth/Machine/Serial)
    fingerprint.py      # Fingerprint-Erhebung
    state.py            # state.json Contract
    jsonio.py           # JSON read/write (atomisch)
    httpclient.py       # HTTP helper (requests)
    systeminfo.py       # Host/IP/OS/Kernel/Binaries
    timeutil.py         # UTC ISO-Zeit
  templates/
    index.html          # Lokale Setup/Diagnostics UI
  web/
    routes_ui.py        # UI-Route `/`
scripts/
  dev_run.sh            # Dev Start per flask run
  device-wifi-autoconnect
  device-wifi-autoconnect.service
docs/
  README.md
  systemd/device-portal.service
  systemd/device-player.service
requirements.txt
```

## 2) Entry Points & Start

- Flask Factory: [app/__init__.py:14](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/__init__.py:14)
- Flask App Objekt: [app/main.py:5](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/main.py:5)
- Dev Start Script: [scripts/dev_run.sh:4](/home/djanebmb/projects/Joormann-Media-Deviceportal/scripts/dev_run.sh:4)
- Prod Service Beispiel (gunicorn): [docs/systemd/device-portal.service:12](/home/djanebmb/projects/Joormann-Media-Deviceportal/docs/systemd/device-portal.service:12)

## 3) Laufzeit-Initialisierung

Beim Start der App werden synchron Dateien initialisiert:
- `ensure_config()` [app/__init__.py:17](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/__init__.py:17)
- `ensure_device()` [app/__init__.py:18](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/__init__.py:18)
- `ensure_fingerprint()` [app/__init__.py:19](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/__init__.py:19)

## 4) Konfigurations- und Dateipfade

ENV-overridable Pfade (mit Defaults):
- `CONFIG_PATH`: `/etc/device/config.json`
- `DEVICE_PATH`: `/etc/device/device.json`
- `FINGERPRINT_PATH`: `/etc/device/fingerprint.json`
- `STATE_PATH`: `/etc/device/state.json`
- `PLAN_PATH`: `/etc/device/plan.json`
- `ASSET_DIR`: `<PORTAL_DIR>/var/assets`

Quelle: [app/core/paths.py:5](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/core/paths.py:5)

## 5) Datenverträge

### state.json
Wird über `update_state()` erzeugt, enthält u. a.:
- `ok`, `mode`, `message`, `hostname`, `ip`
- `panel`: `linked`, `last_error`, `last_http`, `last_check`
- `selected_stream_slug`, `device_slug`, `updated_at`

Quelle: [app/core/state.py:50](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/core/state.py:50)

### plan.json
Wird über `/api/plan/pull` geschrieben:
- `version`, `saved_at`, `admin_base_url`, `device_slug`, `stream_slug`, `plan`

Quelle: [app/api/routes_plan.py:53](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/api/routes_plan.py:53)

## 6) Blueprint-Architektur

Registrierte Blueprints:
- `bp_status` [app/__init__.py:21](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/__init__.py:21)
- `bp_panel` [app/__init__.py:22](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/__init__.py:22)
- `bp_plan` [app/__init__.py:23](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/__init__.py:23)
- `bp_ui` [app/__init__.py:24](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/__init__.py:24)

## 7) Error Handling

Globaler Error Handler fängt alle Exceptions und serialisiert `detail=str(exc)`:
- [app/__init__.py:26](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/__init__.py:26)

Aus Architektur-Sicht funktional, sicherheitlich jedoch kritisch (siehe Security Audit).

## 8) Externe Abhängigkeiten

- Flask
- requests
- gunicorn

Quelle: [requirements.txt:1](/home/djanebmb/projects/Joormann-Media-Deviceportal/requirements.txt:1)

## 9) Deployment-Artefakte im Repo

- systemd Service für Portal vorhanden: [docs/systemd/device-portal.service](/home/djanebmb/projects/Joormann-Media-Deviceportal/docs/systemd/device-portal.service)
- systemd Service für Player-Placeholder vorhanden: [docs/systemd/device-player.service](/home/djanebmb/projects/Joormann-Media-Deviceportal/docs/systemd/device-player.service)
- Keine Nginx-Konfiguration im Repo gefunden.
