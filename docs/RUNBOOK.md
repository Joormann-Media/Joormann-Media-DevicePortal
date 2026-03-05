# Runbook: Joormann-Media DevicePortal

## Abstract
Betriebsanleitung für lokalen Start, Produktionsstart (systemd/gunicorn), Basis-Hardening und Troubleshooting.

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

## 2) Production Start (systemd + gunicorn)

### Referenz Unit
- [docs/systemd/device-portal.service](/home/djanebmb/projects/Joormann-Media-Deviceportal/docs/systemd/device-portal.service)

Wichtige Zeilen:
- WorkingDirectory: `/opt/jm-deviceportal`
- ExecStart: `python3 -m gunicorn -w 2 -b 0.0.0.0:5070 app.main:app`

### Beispiel-Installation
```bash
sudo cp docs/systemd/device-portal.service /etc/systemd/system/device-portal.service
sudo systemctl daemon-reload
sudo systemctl enable --now device-portal.service
sudo systemctl status device-portal.service
```

## 3) Environment / Pfade

Konfigurierbar per ENV:
- `CONFIG_PATH`, `DEVICE_PATH`, `FINGERPRINT_PATH`, `STATE_PATH`, `PLAN_PATH`, `ASSET_DIR`

Quelle: [app/core/paths.py](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/core/paths.py)

Empfehlung Prod:
- `CONFIG_PATH=/etc/device/config.json`
- `DEVICE_PATH=/etc/device/device.json`
- Files Eigentümer auf Service-User (`www-data`) oder passende ACL.

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

### Problem: Persistenzfehler unter `/etc/device/*`
- Rechte/Ownership prüfen:
```bash
sudo ls -la /etc/device
sudo chown -R www-data:www-data /etc/device
```

## 7) Empfohlene Betriebs-Hardening Schritte

1. API-Auth für mutierende Endpoints (`/api/panel/*`, `/api/plan/pull`, `/api/fingerprint/refresh`).
2. TLS-Termination + Restriktion von Port 5070 auf localhost/intern.
3. Rate limiting auf API-Endpunkte.
4. Exception-Detail-Leak im globalen Error Handler entfernen.
