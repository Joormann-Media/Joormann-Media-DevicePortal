# DevicePortal Sync Service

## Ziel
Konfigurierbarer, sicherer Feld-Sync mit dem Adminpanel.

## Endpunkte
- `GET /api/sync/status`
- `GET /api/sync/fields`
- `POST /api/sync/pull-config`
- `POST /api/sync/run`

## Admin-Actions über `/api/sync/run`
- `actionsOnly=true` + `actions=[...]` führt nur Admin-Remote-Aktionen aus.
- Unterstützt u. a.:
  - `system.reboot`
  - `system.portal_restart`
  - `player.restart`
  - `system.portal_update`
  - `player.update`
  - `stream.sync`
  - `overlay.apply`

### `overlay.apply`
- Erwartet `params.overlayState` (JSON mit `flashMessages`, `tickers`, optional `popups`)
- Zusätzliche Parameter:
  - `writeMode`: `replace` (default) | `merge`
  - `includePopups`: `true|false` (default `true`)
  - `flashAutoClear`: `true|false` (default `true`)  
    Bei `true` werden aktive Flash-IDs nach der konfigurierten Gesamtdauer (Summe `durationMs` + kleiner Puffer) automatisch wieder aus `overlay-state.json` entfernt.
- Orientation aus Admin (`horizontal|vertical|rotated_right|rotated_left`) wird serverseitig auf `rotation` gemappt.
- Zusätzlich wird (wenn vorhanden) `overlayState.display.rotationDegrees` aus dem Admin berücksichtigt und auf Flash/Ticker-Rotation addiert.

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
