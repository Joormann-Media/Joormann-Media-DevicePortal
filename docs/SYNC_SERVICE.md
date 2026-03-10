# DevicePortal Sync Service

## Ziel
Konfigurierbarer, sicherer Feld-Sync mit dem Adminpanel.

## Endpunkte
- `GET /api/sync/status`
- `GET /api/sync/fields`
- `POST /api/sync/pull-config`
- `POST /api/sync/run`

## Ablauf
1. Portal zieht Sync-Konfiguration vom Admin (`/api/device/link/sync-config`).
2. Admin->Portal Felder werden lokal angewendet (z. B. Stream, Flags, Sentinel Webhook).
3. Portal baut Runtime-Updates (`runtime.*`).
4. Portal pusht Report an Admin (`/api/device/link/sync-report`).

## Sicherheit
- Für Remote-Run wird bestehende Device/AuthKey bzw. `admin_to_raspi` API-Key-Logik genutzt.
- Admin-Zielcalls nutzen bestehende Device/API-Key (`raspi_to_admin`) Mechanik.

## UI
- Neue Card in `System > Allgemein`:
  - Status
  - letzter Sync
  - aktive Gruppen
  - Buttons: `Sync-Konfiguration aktualisieren`, `Jetzt synchronisieren`

## Refresh-Verhalten
- `refreshStatus()` triggert keinen versteckten `panel/ping` mehr.
- Panel-Ping läuft gezielt (z. B. manueller Link-Refresh) statt in jeder lokalen Statusaktualisierung.
