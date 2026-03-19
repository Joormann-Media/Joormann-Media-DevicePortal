# Installation Helpers

## 1) Portal Basis einrichten

```bash
sudo ./install/setup_portal.sh "$(pwd)" djanebmb
```

- installiert Basis-Pakete (`python3`, `python3-venv`, `python3-pip`, `curl`, `mpg123`)
- installiert `raspotify` (falls noch nicht vorhanden)
- führt auf Raspberry-Pi-Hosts optional `raspi-config nonint do_wifi_country DE` aus
- erstellt/aktualisiert `.venv` im Repo und installiert `requirements.txt`
- legt `<REPO_DIR>/var/data` und `<REPO_DIR>/var/assets` an
- setzt `<REPO_DIR>/var/assets` auf den Service-User (Standard: aktueller `SUDO_USER`) damit Event-/WPS-State-Dateien schreibbar sind
- setzt `<REPO_DIR>/var/data` auf den Service-User (Standard: aktueller `SUDO_USER`) damit Config/State/Plan schreibbar sind
- migriert vorhandene Legacy-Dateien aus `/etc/device/*.json` nach `<REPO_DIR>/var/data/*.json` (falls Ziel noch fehlt)
- schreibt `/etc/default/jm-deviceportal` mit Datenpfaden/ASSET_DIR
- richtet internen Loop-Medienspeicher ein (`/var/lib/deviceportal/media.img` -> `/mnt/deviceportal/media`, ext4, 20G)
- pflegt idempotent `/etc/fstab` mit `loop,nofail` (kein Doppel-Eintrag)
- schreibt und aktiviert eine systemd-Unit mit:
  - `User=<SERVICE_USER>`
  - `WorkingDirectory=<REPO_DIR>`
  - `ExecStart=<REPO_DIR>/.venv/bin/python -m app.main`

Interner Storage-Helfer:
```bash
sudo ./install/setup_internal_storage.sh djanebmb
```

## 2) Netzwerk-Steuerung einrichten

```bash
sudo ./install/setup_netcontrol.sh "$(pwd)" djanebmb
```

- installiert `nmcli`/`rfkill`-relevante Pakete
- deployed `scripts/net/*` nach `/opt/deviceportal/bin`
- legt `/etc/sudoers.d/deviceportal-net` an (NOPASSWD nur für erlaubte Skripte)
- enthält WLAN-Operationen für `scan/connect/profiles` (`wifi_profile.sh`)
- enthält AP-Operationen für Hotspot (`ap_enable.sh`, `ap_disable.sh`, `ap_status.sh`, `ap_clients.sh`)
- enthält Storage-Operationen (`storage_probe.sh`, `storage_mount.sh`, `storage_unmount.sh`)
- ergänzt (falls vorhanden) den Service-User um Gruppe `netdev`
- sudo-Aufrufe erfolgen non-interaktiv (`sudo -n`) über strikt definierte Wrapper-Skripte

## Hinweise

- Das Flask-Backend ruft nur vordefinierte Wrapper-Skripte auf.
- Standard-Skriptverzeichnis im Code: `/opt/deviceportal/bin`, Fallback im Repo: `scripts/net`.
- Optional kann `NETCONTROL_BIN_DIR` gesetzt werden, um den Pfad zu überschreiben.
- Standard-Datenpfade im Code: `<Portal-Ordner>/var/data/*.json` (über `CONFIG_PATH`, `STORAGE_CONFIG_PATH`, `DEVICE_PATH`, `FINGERPRINT_PATH`, `STATE_PATH`, `PLAN_PATH` überschreibbar).
- Standard-Assetpfad im Code: `<Portal-Ordner>/var/assets` (über `ASSET_DIR` überschreibbar).
