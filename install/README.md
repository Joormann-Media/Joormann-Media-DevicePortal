# Installation Helpers

## 1) Portal Basis einrichten

```bash
sudo ./install/setup_portal.sh "$(pwd)" djanebmb
```

- installiert Python-Prereqs
- erstellt/aktualisiert `.venv` im Repo und installiert `requirements.txt`
- legt `<REPO_DIR>/var/data` und `<REPO_DIR>/var/assets` an
- setzt `<REPO_DIR>/var/assets` auf den Service-User (Standard: aktueller `SUDO_USER`) damit Event-/WPS-State-Dateien schreibbar sind
- setzt `<REPO_DIR>/var/data` auf den Service-User (Standard: aktueller `SUDO_USER`) damit Config/State/Plan schreibbar sind
- migriert vorhandene Legacy-Dateien aus `/etc/device/*.json` nach `<REPO_DIR>/var/data/*.json` (falls Ziel noch fehlt)
- schreibt `/etc/default/jm-deviceportal` mit Datenpfaden/ASSET_DIR
- schreibt und aktiviert eine systemd-Unit mit:
  - `User=<SERVICE_USER>`
  - `WorkingDirectory=<REPO_DIR>`
  - `ExecStart=<REPO_DIR>/.venv/bin/python -m app.main`

## 2) Netzwerk-Steuerung einrichten

```bash
sudo ./install/setup_netcontrol.sh "$(pwd)" djanebmb
```

- installiert `nmcli`/`rfkill`-relevante Pakete
- deployed `scripts/net/*` nach `/opt/deviceportal/bin`
- legt `/etc/sudoers.d/deviceportal-net` an (NOPASSWD nur für erlaubte Skripte)
- enthält WLAN-Operationen für `scan/connect/profiles` (`wifi_profile.sh`)
- enthält AP-Operationen für Hotspot (`ap_enable.sh`, `ap_disable.sh`, `ap_status.sh`, `ap_clients.sh`)
- ergänzt (falls vorhanden) den Service-User um Gruppe `netdev`
- sudo-Aufrufe erfolgen non-interaktiv (`sudo -n`) über strikt definierte Wrapper-Skripte

## Hinweise

- Das Flask-Backend ruft nur vordefinierte Wrapper-Skripte auf.
- Standard-Skriptverzeichnis im Code: `/opt/deviceportal/bin`, Fallback im Repo: `scripts/net`.
- Optional kann `NETCONTROL_BIN_DIR` gesetzt werden, um den Pfad zu überschreiben.
- Standard-Datenpfade im Code: `<Portal-Ordner>/var/data/*.json` (über `CONFIG_PATH`, `DEVICE_PATH`, `FINGERPRINT_PATH`, `STATE_PATH`, `PLAN_PATH` überschreibbar).
- Standard-Assetpfad im Code: `<Portal-Ordner>/var/assets` (über `ASSET_DIR` überschreibbar).
