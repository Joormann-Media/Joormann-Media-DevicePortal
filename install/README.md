# Installation Helpers

## 1) Portal Basis einrichten

```bash
sudo ./install/setup_portal.sh /opt/jm-deviceportal www-data
```

- installiert Python-Prereqs
- legt `/etc/device` und `<REPO_DIR>/var/assets` an
- setzt `<REPO_DIR>/var/assets` auf den Service-User (Standard: `www-data`) damit Event-/WPS-State-Dateien schreibbar sind
- installiert/aktiviert `device-portal.service` aus `docs/systemd/device-portal.service`

## 2) Netzwerk-Steuerung einrichten

```bash
sudo ./install/setup_netcontrol.sh /opt/jm-deviceportal www-data
```

- installiert `nmcli`/`rfkill`-relevante Pakete
- deployed `scripts/net/*` nach `/opt/deviceportal/bin`
- legt `/etc/sudoers.d/deviceportal-net` an (NOPASSWD nur für erlaubte Skripte)
- enthält WLAN-Operationen für `scan/connect/profiles` (`wifi_profile.sh`)
- ergänzt (falls vorhanden) den Service-User um Gruppe `netdev`
- sudo-Aufrufe erfolgen non-interaktiv (`sudo -n`) über strikt definierte Wrapper-Skripte

## Hinweise

- Das Flask-Backend ruft nur vordefinierte Wrapper-Skripte auf.
- Standard-Skriptverzeichnis im Code: `/opt/deviceportal/bin`, Fallback im Repo: `scripts/net`.
- Optional kann `NETCONTROL_BIN_DIR` gesetzt werden, um den Pfad zu überschreiben.
- Standard-Assetpfad im Code: `<Portal-Ordner>/var/assets` (über `ASSET_DIR` überschreibbar).
