# Installation Helpers

## 1) Portal Basis einrichten

```bash
sudo ./install/setup_portal.sh /opt/jm-deviceportal
```

- installiert Python-Prereqs
- legt `/etc/device` und `/var/lib/deviceportal/assets` an
- installiert/aktiviert `device-portal.service` aus `docs/systemd/device-portal.service`

## 2) Netzwerk-Steuerung einrichten

```bash
sudo ./install/setup_netcontrol.sh /opt/jm-deviceportal www-data
```

- installiert `nmcli`/`rfkill`-relevante Pakete
- deployed `scripts/net/*` nach `/opt/deviceportal/bin`
- legt `/etc/sudoers.d/deviceportal-net` an (NOPASSWD nur für erlaubte Skripte)

## Hinweise

- Das Flask-Backend ruft nur vordefinierte Wrapper-Skripte auf.
- Standard-Skriptverzeichnis im Code: `/opt/deviceportal/bin`, Fallback im Repo: `scripts/net`.
- Optional kann `NETCONTROL_BIN_DIR` gesetzt werden, um den Pfad zu überschreiben.
