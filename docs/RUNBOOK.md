# Runbook: Joormann-Media DevicePortal

## Abstract
Betriebsanleitung für lokalen Start, Produktionsstart (systemd/venv), Basis-Hardening und Troubleshooting.

## 1) Local Development

### Voraussetzungen
- Python 3.11+
- `pip`

### Start
```bash
cd /home/djanebmb/projects/Joormann-Media-Deviceportal
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
./scripts/dev_run.sh
```

### Dev-URL
- `http://127.0.0.1:5070/`

## 2) Production Start (systemd + venv)

### Provisioning/Installation
```bash
cd /home/djanebmb/projects/Joormann-Media-Deviceportal
sudo ./install/setup_portal.sh "$(pwd)" djanebmb
sudo ./install/setup_netcontrol.sh "$(pwd)" djanebmb
sudo systemctl status device-portal.service
```

Die Unit wird durch `setup_portal.sh` dynamisch erzeugt:
- `User=<SERVICE_USER>`
- `WorkingDirectory=<REPO_DIR>`
- `ExecStart=<REPO_DIR>/.venv/bin/python -m app.main`
- `EnvironmentFile=-/etc/default/jm-deviceportal`

## 3) Environment / Pfade

Konfigurierbar per ENV:
- `CONFIG_PATH`, `DEVICE_PATH`, `FINGERPRINT_PATH`, `STATE_PATH`, `PLAN_PATH`, `ASSET_DIR`

Quelle: [app/core/paths.py](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/core/paths.py)

Empfehlung Prod:
- `CONFIG_PATH=<PORTAL_DIR>/var/data/config.json`
- `DEVICE_PATH=<PORTAL_DIR>/var/data/device.json`
- Files Eigentümer auf Service-User (`djanebmb` im Beispiel) oder passende ACL.

## 4) Reverse Proxy (Nginx) – im Repo nicht enthalten

Im Repo liegt keine Nginx-Config. Empfohlene Minimal-Konfiguration:

```nginx
server {
  listen 443 ssl http2;
  server_name deviceportal.example.tld;

  location / {
    proxy_pass http://127.0.0.1:5070;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }
}
```

## 5) Betriebskontrollen

### Health
```bash
curl -sS http://127.0.0.1:5070/health
```

### Status
```bash
curl -sS http://127.0.0.1:5070/api/status | jq
```

### Journald
```bash
journalctl -u device-portal.service -n 200 --no-pager
journalctl -u device-portal.service -f
```

## 6) Troubleshooting

### Problem: `admin_base_url missing` / `invalid_panel_url`
- `POST /api/panel/test-url` ausführen.
- Prüfen, ob `CONFIG_PATH` schreibbar ist.

### Problem: Register 403 (Token ungültig)
- Token aus richtiger Umgebung (Prod vs. Dev) verwenden.
- Roh-Token, nicht Hash aus Adminliste.

### Problem: Plan Pull 502 / panel_non_json
- Prüfen, ob Panel-URL korrekt und JSON liefert.
- HTML-Login-Seite statt JSON deutet auf Auth/Route-Mismatch hin.

### Problem: Persistenzfehler unter `<PORTAL_DIR>/var/data/*`
- Rechte/Ownership prüfen:
```bash
sudo ls -la /opt/jm-deviceportal/var/data
sudo chown -R djanebmb:djanebmb /opt/jm-deviceportal/var/data
```

## 7) Empfohlene Betriebs-Hardening Schritte

1. API-Auth für mutierende Endpoints (`/api/panel/*`, `/api/plan/pull`, `/api/fingerprint/refresh`).
2. TLS-Termination + Restriktion von Port 5070 auf localhost/intern.
3. Rate limiting auf API-Endpunkte.
4. Exception-Detail-Leak im globalen Error Handler entfernen.
