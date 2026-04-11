# CLAUDE.md — Joormann-Media-Deviceportal

Dieses Dokument ist die primäre Referenz für Claude Code beim Arbeiten mit diesem Repository.
Alle Erkenntnisse stammen aus einer vollständigen Bestandsaufnahme (April 2026).

---

## Projektübersicht

**Was ist das?**  
Ein Flask-basiertes Geräteverwaltungs-Portal für Raspberry-Pi-Geräte im Joormann-Media-Ökosystem.
Das Portal läuft auf dem Gerät selbst und stellt eine Weboberfläche sowie eine REST-API bereit,
über die Netzwerk, Audio, Displays, Speicher und Streams verwaltet werden.

**Zielplattform:** Raspberry Pi (arm64/armhf), Linux  
**Sprache der Codekommentare und Commits:** Deutsch  
**Dokumentation:** `/docs/` (vollständig auf Deutsch)

---

## Technologie-Stack

### Backend
| Technologie        | Version       | Verwendung                              |
|--------------------|---------------|-----------------------------------------|
| Python             | 3.11+         | Hauptsprache                            |
| Flask              | ≥3.0.0, <4   | Web-Framework + Blueprint-Architektur   |
| Gunicorn           | ≥21.2.0       | WSGI-Server (Produktion)                |
| requests           | ≥2.31.0       | HTTP-Client für externe APIs            |
| qrcode             | ≥7.4.2        | QR-Code-Erzeugung                       |

### Frontend
| Technologie        | Version       | Verwendung                              |
|--------------------|---------------|-----------------------------------------|
| Jinja2             | (via Flask)   | HTML-Templates                          |
| Bootstrap          | 5.3.3 (CDN)   | CSS-Framework                           |
| Bootstrap Icons    | 1.11.3 (CDN)  | Icon-Set                                |
| Vanilla JavaScript | ES2020+       | DOM-Manipulation, AJAX                  |

### System-Integration
- **systemd** — Service-Management
- **Bash-Scripts** — 48+ Netzwerk-Wrapper in `/scripts/net/`
- **nmcli / NetworkManager** — WiFi- und Bluetooth-Verwaltung
- **D-Bus** — System-Service-Kommunikation
- **DRM/EDID** — Display-Erkennung via `/sys/class/drm`

---

## Projektstruktur

```
Joormann-Media-Deviceportal/
├── app/
│   ├── __init__.py          # Flask-Factory, Blueprint-Registrierung
│   ├── main.py              # Einstiegspunkt
│   ├── api/                 # REST-API (blueprints, je Feature eine Datei)
│   │   ├── routes_audio.py
│   │   ├── routes_network.py
│   │   ├── routes_overlay.py
│   │   ├── routes_panel.py
│   │   ├── routes_plan.py
│   │   ├── routes_spotify_connect.py
│   │   ├── routes_status.py
│   │   ├── routes_stream.py
│   │   └── routes_sync.py
│   ├── core/                # Business-Logik (kein HTTP-Kontext)
│   │   ├── auth_*.py        # Authentifizierung (local / panel / session)
│   │   ├── config.py        # Konfigurations-Defaults und Normalisierung
│   │   ├── device.py        # Geräte-Identität (UUID, serial, auth_key)
│   │   ├── display.py       # Display-Erkennung
│   │   ├── jsonio.py        # Atomares JSON-Lesen/Schreiben
│   │   ├── netcontrol.py    # Wrapper für Netzwerk-Scripts
│   │   ├── paths.py         # ENV-konfigurierbare Pfade
│   │   ├── state.py         # State-File-Contracts
│   │   └── ...
│   ├── services/            # Zustandsbehaftete Services (Audio, BT, Radio)
│   ├── templates/           # Jinja2-HTML-Templates
│   ├── static/              # CSS, JS, Bilder
│   │   ├── css/             # portal.css, portal-login.css
│   │   └── js/              # portal.js, wifi_setup.js
│   └── web/                 # UI-Routen (kein API, nur HTML)
│       ├── routes_auth.py
│       └── routes_ui.py
├── config/
│   └── ports.env            # PORTAL_PORT=5070, PORTAL_HOST=0.0.0.0
├── docs/                    # Vollständige Dokumentation (Deutsch)
├── install/                 # Installations-Scripts
├── scripts/
│   ├── *.sh                 # Service-Management
│   └── net/                 # 48 Netzwerk-Wrapper-Scripts
├── systemd/                 # systemd-Unit-Dateien
├── var/
│   ├── assets/              # Laufzeit-Assets (nicht im Git)
│   └── data/                # Konfigurations-State (nicht im Git)
│       ├── config.json
│       ├── device.json
│       ├── fingerprint.json
│       └── state.json
├── requirements.txt
└── .gitignore
```

---

## Coding-Konventionen

### Python

**Naming:**
- `snake_case` — Funktionen, Variablen, Module, Dateinamen
- `UPPER_SNAKE_CASE` — Konstanten (z.B. `DEFAULT_AP_PROFILE`, `CONFIG_PATH`)
- Privat/intern: `_leading_underscore` (z.B. `_normalize_node_type()`, `_tail_file()`)
- Blueprints: immer `bp_*` (z.B. `bp_audio`, `bp_network`)

**Imports (Reihenfolge):**
```python
from __future__ import annotations  # Immer zuerst

import os                            # Standard library
import json
from pathlib import Path

from flask import Blueprint, request # Third-party
import requests

from app.core.config import ...      # Projekt-intern (absolut)
from app.core.paths import ...
```

**Type Hints:** Pflicht bei neuen Funktionen.  
**Fehlerbehandlung:** Eigene Exception-Klassen (`NetControlError`), try-except mit spezifischen Typen.  
**JSON-Persistenz:** Immer atomar über `jsonio.py` — erst `.tmp` schreiben, dann `os.replace()`.  
**Pfade:** `pathlib.Path` bevorzugen, keine rohen Strings.  
**Subprocess:** Immer mit Timeout und captured output.

### JavaScript

**Naming:**
- `camelCase` — Variablen, Funktionen (z.B. `networkState`, `getScreenshotInfo()`)
- DOM-Zustands-Variablen: `*State`, `*Initialized`
- Kein TypeScript.

### HTML/CSS

- Bootstrap 5 Utility-Klassen bevorzugen.
- Custom CSS nur in `portal.css` / `portal-login.css`.
- CSS Custom Properties (`--var-name`) für Themes.

### Bash-Scripts

- Fehlerbehandlung: `set -e` oder explizite `|| exit 1`
- Ausgabe als JSON oder `key=value`-Paare (für Python-Parsing in `netcontrol.py`)
- Non-interactive sudo: `sudo -n`

---

## Architektur-Schlüsselmuster

### Blueprint-Muster
Jede API-Datei in `app/api/` exportiert genau ein `bp_*` Blueprint-Objekt.
Registrierung in `app/__init__.py`.

### Auth-Dual-Modus
```
local_system    → Linux-Benutzer via pamtester
panel_remote    → Admin-Panel HTTP-Handshake + Token
```
Modus-Auflösung: `app/core/auth_mode.py`

### Netzwerk-Delegation
Das Portal führt keine direkten Netzwerkoperationen aus —
es delegiert an Shell-Scripts in `/scripts/net/` via `netcontrol.py`.
Die Scripts laufen via `sudo -n` ohne Passwort (NOPASSWD-ACL).

### State-Dateien (Contracts)
| Datei                 | Inhalt                                          |
|-----------------------|-------------------------------------------------|
| `var/data/config.json` | Auth-Keys, WiFi-Profile, Panel-State, Display  |
| `var/data/device.json` | UUID, auth_key, serial, machine_id             |
| `var/data/fingerprint.json` | OS, Kernel, CPU, RAM, Disks, Netzwerk    |
| `var/data/state.json`  | Modus, Hostname, IP, Panel-Link-Status         |

---

## Umgebungsvariablen

| Variable                        | Default                    | Bedeutung                      |
|---------------------------------|----------------------------|--------------------------------|
| `PORTAL_PORT`                   | 5070                       | HTTP-Bind-Port                 |
| `PORTAL_HOST`                   | 0.0.0.0                    | Bind-Adresse                   |
| `FLASK_DEBUG`                   | 0                          | Debug-Modus                    |
| `CONFIG_PATH`                   | var/data/config.json       | Haupt-Konfig                   |
| `DEVICE_PATH`                   | var/data/device.json       | Geräte-Identität               |
| `FINGERPRINT_PATH`              | var/data/fingerprint.json  | Hardware-Fingerprint           |
| `STATE_PATH`                    | var/data/state.json        | Runtime-State                  |
| `PLAN_PATH`                     | var/data/plan.json         | Abspielplan                    |
| `ASSET_DIR`                     | var/assets/                | Asset-Verzeichnis              |
| `NETCONTROL_BIN_DIR`            | /opt/deviceportal/bin      | Netzwerk-Script-Pfad           |
| `PORTAL_SESSION_COOKIE_SECURE`  | —                          | HTTPS-only Cookies             |

---

## Entwicklungs-Setup

```bash
# Venv erstellen und aktivieren
python3 -m venv .venv
source .venv/bin/activate

# Abhängigkeiten installieren
pip install -r requirements.txt

# Entwicklungsserver starten
./scripts/dev_run.sh
# oder direkt:
FLASK_DEBUG=1 python -m flask --app app.main run --port 5070
```

**Produktion:**
```bash
sudo ./install/setup_portal.sh
sudo systemctl enable --now device-portal
```

---

## Bekannte Sicherheitsprobleme (aus Security-Audit)

> Diese Punkte sind dokumentiert. Nicht ohne Auftrag beheben, aber bei neuen
> Features beachten:

- **P0-1:** Keine Auth auf Mutations-Endpoints (bekannt, LAN-only Deployment)
- **P0-2:** Detaillierte Exceptions werden an Clients gesendet
- **P1-1:** Kein Rate-Limiting
- **P1-2:** Secrets werden in der UI angezeigt
- **P1-4:** Unverschlüsselt auf `0.0.0.0:5070` — Reverse Proxy mit TLS empfohlen

---

## Git-Workflow-Regeln

### Commit-Konvention

Conventional Commits auf Deutsch:
```
feat: neue Funktion hinzugefügt
fix: Fehler behoben
chore: Wartung/Aufräumen
refactor: Code-Umbau ohne Funktionsänderung
docs: Dokumentation aktualisiert
```

### Workflow bei Code-Änderungen

1. **Vor jeder Änderung:** Stand sichern
   ```bash
   git add -p   # Interaktiv oder:
   git add <spezifische_dateien>   # KEINE git add . wegen .env-Risiko
   git commit -m "chore: pre-change snapshot vor [Beschreibung]"
   ```

2. **Änderung durchführen** (selbstständig, ohne Rückfragen bei Standard-Operationen)

3. **Nach der Änderung:** Ergebnis committen
   ```bash
   git add <geänderte_dateien>
   git commit -m "feat/fix/chore: [Was wurde gemacht]"
   ```

4. **Abschließend pushen:**
   ```bash
   git push origin main   # oder aktuellen Branch
   ```

> **Sicherheitshinweis zu `git add .`:** Niemals blind `git add .` verwenden.
> Die Dateien `var/data/*.json` (device.json, config.json mit auth_key!) sind in
> `.gitignore` — aber neue sensible Dateien könnten unbeabsichtigt gestaged werden.
> Immer spezifische Dateien oder `git add -p` verwenden.

### Dateien NIEMALS committen
- `var/data/` (device.json, config.json, fingerprint.json, state.json)
- `var/assets/`
- `.venv/`
- `*.pyc`, `__pycache__/`
- `.env`-Dateien

---

## Arbeitsweise für Claude Code

### Selbstständig vorgehen bei
- Feature-Implementierungen nach Beschreibung
- Bug-Fixes ohne Nebenwirkungen
- Refactoring innerhalb bestehender Muster
- Dokumentations-Updates
- Script-Ergänzungen in `/scripts/net/`

### Rückfrage bei
- Änderungen an Auth-Logik (`app/core/auth_*.py`)
- Änderungen an State-Contracts (`app/core/state.py`, `app/core/jsonio.py`)
- Neuen externen Dependencies (requirements.txt)
- Änderungen an systemd-Unit-Dateien
- Destructive Operationen (Datei löschen, Schema-Änderungen)

### Code-Qualitäts-Standards
- Keine neuen Features ohne Bedarf
- Kein spekulativer Code für "hypothetische Zukunft"
- Type Hints bei neuen Funktionen
- Atomare JSON-Operationen via `jsonio.py`
- Pfade über `app/core/paths.py` (ENV-konfigurierbar)
- Fehler-Typen explizit (`NetControlError`, nicht generische `Exception`)

---

## Kein Test-Setup

Das Projekt hat aktuell **keine automatisierten Tests** (kein pytest, kein unittest).
Bei neuen Features: manuelle Tests via `./scripts/dev_run.sh` und direkte API-Calls
mit curl oder dem Browser.

## Kein CI/CD

Kein `.github/workflows/`, kein `.gitlab-ci.yml`. Deployments sind manuell via
`setup_portal.sh` + systemd.
