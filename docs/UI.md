# UI Overview

## Abstract
Die Startseite `/` wurde auf eine tab-basierte SaaS-UI umgestellt (Bootstrap 5 + AJAX), gesteuert über `app/static/js/portal.js`.

## Struktur

- Hero mit Runtime-Badge (`ONLINE / LINKED` etc.)
- Tab 1: `Status & Link`
- Tab 2: `Netzwerk & WLAN`
- Tab 3: `Stream Einstellungen` (Platzhalter)
- Tab 4: `System Einstellungen` (DNS-Fix Aktion)

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

## System Aktionen

- `Tailscale DNS-Override deaktivieren` -> `POST /api/system/tailscale/disable-dns`

## Response-Konvention für Netzwerk-Endpunkte

Success:

```json
{ "ok": true, "data": { "...": "..." } }
```

Error:

```json
{ "ok": false, "error": { "code": "...", "message": "...", "detail": "..." } }
```
