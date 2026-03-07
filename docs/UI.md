# UI Overview

## Abstract
Die Startseite `/` wurde auf eine tab-basierte SaaS-UI umgestellt (Bootstrap 5 + AJAX), gesteuert über `app/static/js/portal.js`.

## Struktur

- Hero mit Runtime-Badge (`ONLINE / LINKED` etc.) und Update-Status (`Up to date` / `Update verfügbar`)
- Tab 1: `Status & Link`
- Tab 2: `Netzwerk & WLAN`
- Tab 3: `Storage`
- Tab 4: `Stream Einstellungen` (Platzhalter)
- Tab 5: `System Einstellungen` (DNS-Fix Aktion)

## Frontend-Dateien

- `app/templates/index.html`
- `app/static/js/portal.js`
- `app/static/css/portal.css`

## AJAX Controller (`portal.js`)

Zentrale Funktionen:

- `fetchJson(url, options)`
- `toast(message, type)`
- `renderStatus(data)`
- `renderNetwork(data)`
- `bindButtons()`

## Status/Link Aktionen

- `Refresh Status` -> `GET /api/status/state`
- `Refresh Fingerprint` -> `POST /api/status/fingerprint/refresh`
- `Pull Plan` -> `POST /api/plan/pull`
- `Check Panel Link` -> `GET /api/panel/link-status`
- `Unlink` -> `POST /api/panel/unlink` (mit Confirm-Modal)

Zusätzliche Panel-Aktionen:

- `Test/Save URL` -> `POST /api/panel/test-url`
- `Panel Ping` -> `POST /api/panel/ping`
- `Panel Register` -> `POST /api/panel/register`

## Display-Bereich (Status-Tab)

- Neue Karte `Displays` im Tab `Status & Link`.
- Zeigt pro erkanntem Display:
  - Displayname / Connector / Verbindung
  - Hersteller/Modell
  - Aktueller und bevorzugter Modus
  - Refresh
  - Physische Größe / EDID-Status
  - Montage-Ausrichtung + resultierende Rotation
- Pro Display editierbar:
  - `mount_orientation`
  - `active`
- Speichern läuft per AJAX über `POST /api/display/config`.
- Nach dem Speichern:
  - Status wird live neu geladen
  - bei bestehendem Panel-Link wird ein Sync zum Adminpanel angestoßen.

## Netzwerk Aktionen

- `Refresh Network Info` -> `GET /api/network/info`
- `WPS starten` -> `POST /api/network/wps`
- `Wi-Fi Toggle` -> `POST /api/network/wifi/toggle`
- `Bluetooth Toggle` -> `POST /api/network/bluetooth/toggle`
- `LAN Toggle` -> `POST /api/network/lan/toggle`
- `Scan` (WLAN Netze) -> `GET /api/wifi/scan`
- `Verbinden` (SSID) -> `POST /api/wifi/connect`
- `Profile Refresh` -> `GET /api/wifi/profiles`
- `Profile Add` -> `POST /api/wifi/profiles/add`
- `Profile Prefer` -> `POST /api/wifi/profiles/prefer`
- `Profile Up` -> `POST /api/wifi/profiles/up`
- `Profile Delete` -> `POST /api/wifi/profiles/delete`
- `Profile Apply` -> `POST /api/wifi/profiles/apply`
- `WPS Live Status` -> `GET /api/network/wifi/wps/status`
- `WLAN Runtime Status` -> `GET /api/network/wifi/status`
- `WLAN Logs` -> `GET /api/network/wifi/logs`
- `AP Status` -> `GET /api/network/ap/status`
- `AP Toggle` -> `POST /api/network/ap/toggle`
- `AP Clients` -> `GET /api/network/ap/clients`

## Netzwerk-Tab Inhalte

- Hinweisbox für WPS-Bedienreihenfolge (Router-Taste zuerst, dann Portal innerhalb 2 Minuten).
- WLAN Statuskarte inkl. `wpa_state`.
- WLAN Scanliste mit:
  - `WPS Ziel`
  - `Verbinden`
  - `WPS`
- Gespeicherte WLANs inkl. `Verbinden/Prefer/Löschen`.
- Manuelle WLAN-Konfiguration inkl. Hidden-SSID-Option.
- Live-Ereignisbereich (`WPS / Ereignisse`) mit laufendem Polling.
- AP-Bereich mit Status, AP-Buttons und AP-Clientliste (Live-Polling mit Delta-Flash für neue Clients).
- AP-Hinweis im UI: Hotspot verbinden und `http://192.168.4.1` öffnen.

## Storage-Tab Inhalte

- Interner Speicherbereich (Loop-Storage) mit:
  - Statusbadge
  - Image-/Mount-Pfad
  - FS-Info
  - Gesamt/Belegt/Frei
  - Nutzungsbalken (Windows-ähnliche Laufwerksansicht)
- Laufwerksübersicht (`drives`) als Karten mit Nutzungsbalken/Status.
- Externe Gerätebereiche:
  - `Neu erkannte Geräte` (Hinzufügen/Ignorieren)
  - `Ignorierte Geräte` (Zurückholen)
  - `Registrierte Speicher` (Mount/Unmount/Toggle/Entfernen + `Dateien verwalten`)
- Polling mit Delta-Logik (Toast nur bei echten Änderungen: neu erkannt, getrennt, gemountet).
- Integrierter File-Manager (ohne Reload):
  - Storage-Übersicht slidet aus, File-Manager slidet ein
  - 3 Spalten:
    - Verzeichnisse (inkl. Breadcrumb)
    - Inhalte (inkl. Select all / Unselect all / Delete selected)
    - Upload + Live-Vorschau/Details
  - `Zurück` blendet wieder die Storage-Übersicht ein.
  - Sicherheits-Härtung:
    - Symlink-Einträge werden als blockiert markiert
    - Delete selected nutzt ein Bestätigungs-Modal mit Pflicht-Eingabe `DELETE`
    - große Dateien werden nicht blind in Vorschau geladen
  - Uploadbox (Drag&Drop + Dateiauswahl) mit AJAX-Fortschritt pro Datei und Live-Refresh der Dateiliste.

## System Aktionen

- `Tailscale DNS-Override deaktivieren` -> `POST /api/system/tailscale/disable-dns`
- `Portal Update starten` -> `POST /api/system/portal/update`
- `Portal Update Status/Log (live + letztes Update)` -> `GET /api/system/portal/update/status`

## Response-Konvention für Netzwerk-Endpunkte

Success:

```json
{ "ok": true, "data": { "...": "..." } }
```

Error:

```json
{ "ok": false, "error": { "code": "...", "message": "...", "detail": "..." } }
```
