# API Reference: Joormann-Media DevicePortal

## Abstract
Vollständige Endpoint-Inventarisierung auf Basis der aktuellen Flask-Blueprints (`routes_status`, `routes_panel`, `routes_plan`, `routes_network`, `routes_ui`).

## Auth-Modell
- Session-Login ist aktiv.
- Login-Endpoints:
  - `GET /login`
  - `POST /login`
  - `POST /logout`
  - `GET /api/auth/mode`
  - `GET /api/auth/status`
- Modus-Resolver:
  - `local_system` wenn kein Panel-Link mit User-Verknüpfung existiert.
  - `panel_remote` wenn Panel-Link + verknüpfte User vorhanden sind.
- Ausnahme ohne Session:
  - `GET /health`
  - `POST /api/panel/admin-sync-payload` (weiterhin Device-Credentials-basiert)

## Error-Format
Netzwerk-/WLAN-Endpunkte liefern ein einheitliches Grundschema:
- Success: `{ "ok": true, "success": true, "message": "...", "data": {...}, "error_code": "" }`
- Error: `{ "ok": false, "success": false, "message": "...", "data": {}, "error_code": "..." }`
- Zusätzlich bleibt `error: { code, message, detail }` für Kompatibilität erhalten.

## Endpoint-Übersicht

| Group | Method | Path | Handler |
|---|---|---|---|
| auth | GET | `/login` | `routes_auth.login_page` |
| auth | POST | `/login` | `routes_auth.login_submit` |
| auth | POST | `/logout` | `routes_auth.logout_submit` |
| auth | GET | `/api/auth/mode` | `routes_auth.api_auth_mode` |
| auth | GET | `/api/auth/status` | `routes_auth.api_auth_status` |
| auth | GET | `/api/auth/local-users` | `routes_auth.api_auth_local_users` |
| public/ui | GET | `/` | `routes_ui.index` |
| status | GET | `/health` | `routes_status.health` |
| status | GET | `/api/status` | `routes_status.api_status` |
| status | GET | `/api/display/info` | `routes_status.api_display_info` |
| status | GET | `/api/fingerprint` | `routes_status.api_fingerprint` |
| status | POST | `/api/fingerprint/refresh` | `routes_status.api_fingerprint_refresh` |
| status | POST | `/api/status/fingerprint/refresh` | `routes_status.api_status_fingerprint_refresh` |
| status | GET | `/api/state` | `routes_status.api_state` |
| status | GET | `/api/status/state` | `routes_status.api_status_state` |
| panel | POST | `/api/panel/test-url` | `routes_panel.api_panel_test_url` |
| panel | POST | `/api/panel/ping` | `routes_panel.api_panel_ping` |
| panel | POST | `/api/panel/register` | `routes_panel.api_panel_register` |
| panel | POST | `/api/panel/admin-sync-payload` | `routes_panel.api_panel_admin_sync_payload` |
| panel | GET | `/api/panel/link-status` | `routes_panel.api_panel_link_status` |
| panel | POST | `/api/panel/unlink` | `routes_panel.api_panel_unlink` |
| plan | POST | `/api/plan/pull` | `routes_plan.api_plan_pull` |
| plan | GET | `/api/plan/current` | `routes_plan.api_plan_current` |
| network | GET | `/api/network/info` | `routes_network.api_network_info` |
| network | GET | `/api/network/display/info` | `routes_network.api_network_display_info` |
| network | POST | `/api/display/config` | `routes_network.api_display_config` |
| network | POST | `/api/network/display/config` | `routes_network.api_network_display_config` |
| network | POST | `/api/network/wifi/toggle` | `routes_network.api_network_wifi_toggle` |
| network | POST | `/api/network/bluetooth/toggle` | `routes_network.api_network_bluetooth_toggle` |
| network | POST | `/api/network/lan/toggle` | `routes_network.api_network_lan_toggle` |
| network | POST | `/api/network/wps` | `routes_network.api_network_wps` |
| network | GET | `/api/wifi/scan` | `routes_network.api_wifi_scan` |
| network | POST | `/api/wifi/connect` | `routes_network.api_wifi_connect` |
| network | GET | `/api/wifi/profiles` | `routes_network.api_wifi_profiles` |
| network | POST | `/api/wifi/profiles/add` | `routes_network.api_wifi_profiles_add` |
| network | POST | `/api/wifi/profiles/delete` | `routes_network.api_wifi_profiles_delete` |
| network | POST | `/api/wifi/profiles/prefer` | `routes_network.api_wifi_profiles_prefer` |
| network | POST | `/api/wifi/profiles/up` | `routes_network.api_wifi_profiles_up` |
| network | POST | `/api/wifi/profiles/apply` | `routes_network.api_wifi_profiles_apply` |
| network | GET | `/api/network/wifi/status` | `routes_network.api_network_wifi_status` |
| network | GET | `/api/network/wifi/saved` | `routes_network.api_network_wifi_saved` |
| network | GET | `/api/network/wifi/scan` | `routes_network.api_network_wifi_scan` |
| network | POST | `/api/network/wifi/connect` | `routes_network.api_network_wifi_connect` |
| network | POST | `/api/network/wifi/select` | `routes_network.api_network_wifi_select` |
| network | POST | `/api/network/wifi/remove` | `routes_network.api_network_wifi_remove` |
| network | POST | `/api/network/wifi/disconnect` | `routes_network.api_network_wifi_disconnect` |
| network | POST | `/api/network/wifi/toggle` | `routes_network.api_network_wifi_toggle_alias` |
| network | POST | `/api/network/wifi/wps/start` | `routes_network.api_network_wifi_wps_start` |
| network | GET | `/api/network/wifi/wps/status` | `routes_network.api_network_wifi_wps_status` |
| network | GET | `/api/network/wifi/logs` | `routes_network.api_network_wifi_logs` |
| network | GET | `/api/network/ap/status` | `routes_network.api_network_ap_status` |
| network | POST | `/api/network/ap/toggle` | `routes_network.api_network_ap_toggle` |
| network | GET | `/api/network/ap/clients` | `routes_network.api_network_ap_clients` |
| network | GET | `/api/network/storage/status` | `routes_network.api_network_storage_status` |
| network | POST | `/api/network/storage/register` | `routes_network.api_network_storage_register` |
| network | POST | `/api/network/storage/ignore` | `routes_network.api_network_storage_ignore` |
| network | POST | `/api/network/storage/unignore` | `routes_network.api_network_storage_unignore` |
| network | POST | `/api/network/storage/remove` | `routes_network.api_network_storage_remove` |
| network | POST | `/api/network/storage/mount` | `routes_network.api_network_storage_mount` |
| network | POST | `/api/network/storage/unmount` | `routes_network.api_network_storage_unmount` |
| network | POST | `/api/network/storage/toggle-enabled` | `routes_network.api_network_storage_toggle_enabled` |
| network | POST | `/api/network/storage/toggle-automount` | `routes_network.api_network_storage_toggle_automount` |
| network | GET | `/api/network/storage/file-manager/tree` | `routes_network.api_network_storage_file_manager_tree` |
| network | GET | `/api/network/storage/file-manager/list` | `routes_network.api_network_storage_file_manager_list` |
| network | GET | `/api/network/storage/file-manager/preview` | `routes_network.api_network_storage_file_manager_preview` |
| network | POST | `/api/network/storage/file-manager/delete` | `routes_network.api_network_storage_file_manager_delete` |
| network | GET | `/api/network/storage/file-manager/file` | `routes_network.api_network_storage_file_manager_file` |
| network | POST | `/api/network/storage/file-manager/upload` | `routes_network.api_network_storage_file_manager_upload` |
| network | POST | `/api/system/tailscale/disable-dns` | `routes_network.api_system_tailscale_disable_dns` |
| network | POST | `/api/system/portal/update` | `routes_network.api_system_portal_update` |
| network | GET | `/api/system/portal/update/status` | `routes_network.api_system_portal_update_status` |
| spotify_connect | GET | `/api/spotify-connect/status` | `routes_spotify_connect.api_spotify_connect_status` |
| spotify_connect | POST | `/api/spotify-connect/start` | `routes_spotify_connect.api_spotify_connect_start` |
| spotify_connect | POST | `/api/spotify-connect/stop` | `routes_spotify_connect.api_spotify_connect_stop` |
| spotify_connect | POST | `/api/spotify-connect/restart` | `routes_spotify_connect.api_spotify_connect_restart` |
| spotify_connect | POST | `/api/spotify-connect/refresh` | `routes_spotify_connect.api_spotify_connect_refresh` |

Quelle der Route-Definitionen:
- [app/api/routes_status.py](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/api/routes_status.py)
- [app/api/routes_panel.py](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/api/routes_panel.py)
- [app/api/routes_plan.py](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/api/routes_plan.py)
- [app/api/routes_network.py](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/api/routes_network.py)
- [app/api/routes_spotify_connect.py](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/api/routes_spotify_connect.py)
- [app/web/routes_ui.py](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/web/routes_ui.py)

---

## 1) GET `/health`
- **Auth:** none
- **Query:** none
- **Body:** none
- **200:** `{ "ok": true }`

```bash
curl -sS http://127.0.0.1:5070/health
```

---

## 2) GET `/api/status`
- **Auth:** none
- **Response:**
  - `config` (persistierte Config)
  - `device` (mit maskiertem `auth_key`)
  - `fingerprint` (short)
  - `display` (Snapshot mit `displays[]`, `primary_display`, `display_summary`, `warnings`)
  - `app_update` (lokaler/remote Git-Stand + Update verfügbar)
  - `state`

**200 example**
```json
{
  "ok": true,
  "config": {"admin_base_url": "https://..."},
  "device": {"device_uuid": "...", "auth_key": "********abcd"},
  "fingerprint": {"hostname": "...", "kernel": "..."},
  "display": {"display_summary": {"total": 1, "connected": 1}, "displays": []},
  "app_update": {"available": false, "local_branch": "main", "local_commit": "abc...", "remote_commit": "abc...", "error": ""},
  "state": {"mode": "setup", "panel": {"linked": false}}
}
```

---

## 5c) GET `/api/display/info`
- **Auth:** none
- **200:** `{ "ok": true, "display": { ...snapshot... } }`

Snapshot enthält:
- `displays[]` (Connector, Status, Modus, EDID-Metadaten, Orientierung)
- `primary_display`
- `display_summary`
- `warnings`

---

## 3) GET `/api/fingerprint`
- **Auth:** none
- **200:** `{ "ok": true, "fingerprint": { ...full... } }`

## 4) POST `/api/fingerprint/refresh`
- **Auth:** none
- **Body:** none
- **200:** refreshed fingerprint

```bash
curl -sS -X POST http://127.0.0.1:5070/api/fingerprint/refresh
```

---

## 5) GET `/api/state`
- **Auth:** none
- **200:** `{ "ok": true, "state": {...} }`

## 5a) POST `/api/status/fingerprint/refresh`
- **Auth:** none
- Alias zu `/api/fingerprint/refresh`.
- **Body:** none
- **200:** refreshed fingerprint

## 5b) GET `/api/status/state`
- **Auth:** none
- Alias zu `/api/state`.
- **200:** `{ "ok": true, "state": {...} }`

---

## 6) POST `/api/panel/test-url`
- **Auth:** none
- **Body JSON:**
```json
{ "url": "https://admin.example.tld" }
```
- **200:**
```json
{ "ok": true, "base_url": "https://admin.example.tld", "http": 200 }
```
- **400:** missing/unreachable URL
- **500:** persist failure for config write

```bash
curl -sS -X POST http://127.0.0.1:5070/api/panel/test-url \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://admin.xxlprint-wesel.de"}'
```

---

## 6b) POST `/api/display/config`
- **Auth:** none
- **Body JSON:**
```json
{
  "connector": "HDMI-A-1",
  "mount_orientation": "portrait_cable_right",
  "active": true
}
```
- **200:** persistiert lokale Display-Konfiguration und liefert aktuellen Snapshot.
- **400:** ungültige Payload / ungültige Orientierung.
- **500:** Config konnte nicht geschrieben werden.

Alias:
- `POST /api/network/display/config`

---

## 7) POST `/api/panel/ping`
- **Auth:** none
- **Body JSON (optional):**
```json
{ "admin_base_url": "https://admin.example.tld" }
```
- **Payload to Panel (internal):** `deviceUuid`, `authKey`, `hostname`, `ipAddress`, `fingerprint{...}`
- **200/other:**
  - `ok` (true bei `http == 200`)
  - `http`
  - `resolved_url`
  - `panel_link_state`
  - `response` (panel response)

**Edgecases**
- `400 admin_base_url missing`
- `400 invalid_panel_url`
- `502` bei Transportfehler

---

## 8) POST `/api/panel/register`
- **Auth:** none
- **Body JSON:**
```json
{
  "registration_token": "...",
  "admin_base_url": "https://admin.example.tld"
}
```
- `registration_token` fallback auf Config (`registration_token`).
- **Payload to Panel (internal):**
  - `registrationToken`, `deviceUuid`, `authKey`, `hostname`, `ipAddress`
  - `piSerial`, `machineId` (top-level)
  - `fingerprint{...}`, `panelRegisterPath`
- **200:** register linked
- **400:** validation/token/path errors
- **502:** transport failure

```bash
curl -sS -X POST http://127.0.0.1:5070/api/panel/register \
  -H 'Content-Type: application/json' \
  -d '{"admin_base_url":"https://admin.xxlprint-wesel.de","registration_token":"TOKEN"}'
```

---

## 9) GET `/api/panel/link-status`
- **Auth:** none
- **200:** link flags + panel paths + device slug

---

## 10) POST `/api/panel/unlink`
- **Auth:** none
- **Body:** none
- **200:** reset von `registration_token` + panel link state

---

## 11) POST `/api/panel/validate-token`
- **Auth:** none
- **Body JSON:**
```json
{
  "admin_base_url": "https://admin.example.tld",
  "registration_token": "TOKEN"
}
```
- Prüft URL + Token gegen Panel-Endpoint `/api/device/link/verify-token`.
- **200:** `{ "ok": true, "valid": true, ... }`
- **400:** ungültige Eingaben / Token ungültig
- **502:** Transportfehler

---

## 12) GET `/api/panel/search-users`
## 13) GET `/api/panel/search-customers`
- **Auth:** none
- **Query:** `q`, `registration_token`, optional `admin_base_url`
- Proxy-Live-Suche für Setup-Wizard Schritt 3.
- **200:** normalisierte `items[]` mit `id`, `name`, `subtitle`.

---

## 14) POST `/api/panel/assign`
- **Auth:** none
- **Body JSON:**
```json
{
  "admin_base_url": "https://admin.example.tld",
  "registration_token": "TOKEN",
  "target_type": "user",
  "target_id": "123"
}
```
- Sendet optionale User/Customer-Zuordnung an `/api/device/link/assign`.
- **200:** Zuordnung erfolgreich.

---

## 15) POST `/api/plan/pull`
- **Auth:** none
- **Body JSON (optional fields, aber faktisch required via fallback chain):**
```json
{
  "admin_base_url": "https://admin.example.tld",
  "deviceSlug": "device-slug",
  "streamSlug": "stream-slug"
}
```
- **Validierung:** fehlende Felder => 400 (`missing_admin_base_url`, `missing_device_slug`, `missing_stream_slug`)
- **200:** schreibt `plan.json`, aktualisiert state auf `play`
- **502:** nicht-JSON/HTML/transportfehler

```bash
curl -sS -X POST http://127.0.0.1:5070/api/plan/pull \
  -H 'Content-Type: application/json' \
  -d '{"admin_base_url":"https://admin.xxlprint-wesel.de","deviceSlug":"dev1","streamSlug":"main"}'
```

---

## 16) GET `/api/plan/current`
- **Auth:** none
- **200:** `{ "ok": true, "plan": {...} }`
- **404:** `{ "ok": false, "error": "plan_missing", "path": "..." }`

---

## 17) GET `/`
- HTML Setup/Diagnostics Oberfläche.
- Nutzt JS `fetch()` auf oben genannte JSON-Endpunkte.

---

## 18) GET `/api/network/info`
- **Auth:** none
- **200:** Netzwerkstatus aus Wrapper-Script (`network_info.sh`)

---

## Storage (lokal, ohne Adminpanel-DB)

Neue lokale Storage-Endpunkte arbeiten mit `var/data/config-storage.json`:

- `GET /api/network/storage/status`: merged Erkennung + registrierte Geräte (known/new/missing/mounted).
- `POST /api/network/storage/register`: neues Gerät in lokale Config übernehmen.
- `POST /api/network/storage/ignore`: neu erkanntes Gerät ignorieren.
- `POST /api/network/storage/unignore`: ignoriertes Gerät wieder freigeben.
- `POST /api/network/storage/remove`: registriertes Gerät aus lokaler Config entfernen.
- `POST /api/network/storage/mount`: manuelles Mounten eines registrierten Geräts.
- `POST /api/network/storage/unmount`: manuelles Unmounten eines registrierten Geräts.
- `POST /api/network/storage/toggle-enabled`: Gerät aktiv/deaktiviert markieren.
- `POST /api/network/storage/toggle-automount`: Auto-Mount pro Gerät ein/aus.
- `GET /api/network/storage/file-manager/tree`: Verzeichnis-Browser inkl. Breadcrumb für einen relativen Pfad.
- `GET /api/network/storage/file-manager/list`: Dateien/Ordner eines relativen Pfads.
- `GET /api/network/storage/file-manager/preview`: Metadaten + Vorschauinfos für Datei/Ordner.
- `POST /api/network/storage/file-manager/delete`: sichere Löschung ausgewählter relativer Pfade (inkl. Confirm-Word).
- `GET /api/network/storage/file-manager/file`: kontrollierte Dateiausgabe für Preview (z. B. Bilder/PDF).
- `POST /api/network/storage/file-manager/upload`: Upload in das aktuell gewählte Verzeichnis (`multipart/form-data`, `files[]`).

`POST /api/network/storage/file-manager/delete` erwartet zusätzlich:
- `confirm_word` (`DELETE`)
- `confirm_count` (Anzahl der selektierten Pfade)

Wiedererkennung erfolgt primär über `UUID`, fallback `PARTUUID`; `/dev/sdX` wird nur als `last_seen_device_path` behandelt.

**200 example**
```json
{
  "ok": true,
  "data": {
    "hostname": "device-host",
    "interfaces": {
      "lan": {"ifname": "eth0", "present": true, "enabled": true, "carrier": true, "ip": "192.168.1.10", "mac": "aa:bb:cc:dd:ee:ff"},
      "wifi": {"ifname": "wlan0", "present": true, "enabled": true, "connected": true, "ssid": "Office", "signal": 68, "ip": "192.168.1.11", "mac": "11:22:33:44:55:66", "radio": "enabled"},
      "bluetooth": {"present": true, "enabled": false}
    },
    "routes": {"gateway": "192.168.1.1", "dns": ["1.1.1.1", "8.8.8.8"]},
    "tailscale": {"present": true, "ip": "100.x.y.z"},
    "tools": {"nmcli": true, "rfkill": true, "tailscale": true}
  }
}
```

---

## 15) GET `/api/wifi/scan`
- scannt verfügbare WLAN-Netze (sortiert nach Signal)
- Response:
```json
{
  "ok": true,
  "data": {
    "ifname": "wlan0",
    "networks": [
      {"in_use": false, "ssid": "Office", "signal": 78, "security": "WPA2"},
      {"in_use": true, "ssid": "Guest", "signal": 62, "security": "WPA2"}
    ]
  }
}
```

## 16) POST `/api/wifi/connect`
- Body:
```json
{"ssid":"Office","password":"secret","ifname":"wlan0"}
```
- verbindet direkt per `nmcli`, speichert Profil in Config (`wifi_profiles`)

## 17) GET `/api/wifi/profiles`
- merged Sicht aus:
  - konfigurierten Profilen (`config.json`)
  - vorhandenen NetworkManager-Profilen

## 18) POST `/api/wifi/profiles/add`
- Body:
```json
{"ssid":"Office","password":"secret","priority":80,"autoconnect":true,"ifname":"wlan0"}
```
- erstellt/aktualisiert Profil, setzt NM-Autoconnect-Settings und persistiert in Config

## 19) POST `/api/wifi/profiles/delete`
- Body: `{"ssid":"Office"}`

## 20) POST `/api/wifi/profiles/prefer`
- Body: `{"ssid":"Office"}`
- setzt preferred SSID (`priority=999`, `autoconnect=true`)

## 21) POST `/api/wifi/profiles/up`
- Body: `{"ssid":"Office"}`
- aktiviert explizit ein Profil

## 22) POST `/api/wifi/profiles/apply`
- setzt alle Profile entsprechend Priorität/Autoconnect
- versucht danach die beste Verbindung hochzuziehen

## 23) POST `/api/system/tailscale/disable-dns`
- schaltet Tailscale DNS-Override aus (`tailscale set --accept-dns=false`)

---

## WLAN/WPS Live-Endpunkte

### GET `/api/network/wifi/status`
- Liefert `wlan0` Laufzeitstatus (`radio`, `device_state`, `wpa_state`, `ssid`, `ip`, `signal`).

### POST `/api/network/wifi/wps/start`
- Startet WPS (optional mit `target_ssid`/`target_bssid`).
- Setzt internen WPS-Laufzeitstatus.

### GET `/api/network/wifi/wps/status`
- Liefert laufende WPS-Phase:
  - `idle`
  - `started`
  - `router_search`
  - `auth`
  - `dhcp_request`
  - `connected`
  - `timeout`
- Enthält zusätzlich aktuellen WLAN-Status.

### GET `/api/network/wifi/logs`
- Gibt letzte Netzwerk-/WPS-Ereignisse zurück (`events`), erzeugt durch API-Aktionen und WPS-Flow.

**Error example**
```json
{
  "ok": false,
  "error": {
    "code": "network_info_failed",
    "message": "Failed to read network status",
    "detail": "nmcli not found"
  }
}
```

```bash
curl -sS http://127.0.0.1:5070/api/network/info
```

---

## 15) POST `/api/network/wifi/toggle`
- **Auth:** none
- **Body JSON:**
```json
{ "enabled": true }
```
- **200:** `{ "ok": true, "data": { "enabled": true, "stdout": "..." } }`
- **400:** invalid payload / execution error

```bash
curl -sS -X POST http://127.0.0.1:5070/api/network/wifi/toggle \
  -H 'Content-Type: application/json' \
  -d '{"enabled":false}'
```

---

## 16) POST `/api/network/bluetooth/toggle`
- **Auth:** none
- **Body JSON:**
```json
{ "enabled": false }
```
- **200:** `{ "ok": true, "data": { "enabled": false, "stdout": "..." } }`
- **400:** invalid payload / execution error

```bash
curl -sS -X POST http://127.0.0.1:5070/api/network/bluetooth/toggle \
  -H 'Content-Type: application/json' \
  -d '{"enabled":true}'
```

---

## 17) POST `/api/network/lan/toggle`
- **Auth:** none
- **Body JSON:**
```json
{ "enabled": true, "ifname": "eth0" }
```
- `ifname` optional, default `eth0`.
- Interface ist whitelisted (aktuell nur `eth0`).
- **200:** `{ "ok": true, "data": { "ifname": "eth0", "enabled": true, "stdout": "..." } }`
- **400:** invalid payload / invalid interface / execution error

```bash
curl -sS -X POST http://127.0.0.1:5070/api/network/lan/toggle \
  -H 'Content-Type: application/json' \
  -d '{"enabled":false,"ifname":"eth0"}'
```

---

## 18) POST `/api/network/wps`
- **Auth:** none
- **Body JSON (optional):**
```json
{ "ifname": "wlan0" }
```
- Default Interface: `wlan0`.
- **200:** `{ "ok": true, "data": { "ifname": "wlan0", "stdout": "..." } }`
- **400:** wps failed / execution error

```bash
curl -sS -X POST http://127.0.0.1:5070/api/network/wps \
  -H 'Content-Type: application/json' \
  -d '{}'
```

---

## Netzwerk-Fehlerformat (neu)
Die `/api/network/*` Endpunkte liefern konsistent:

```json
{
  "ok": false,
  "error": {
    "code": "machine_code",
    "message": "human readable",
    "detail": "optional runtime detail"
  }
}
```

---

## Konsistenzbewertung

### Positiv
- JSON-Responses durchgehend mit `ok`-Flag.
- Viele Fehlerfälle liefern klare maschinenlesbare Strings.

### Inkonsistenzen
- Unterschiedliche Fehler-Detailtiefe je Endpoint (`error` only vs. `panel_link_state` + `panel_response`).
- Globaler Exception-Handler liefert `detail` im Klartext.

### Vorschlag für einheitliches Fehlerformat
```json
{
  "ok": false,
  "error": "machine_code",
  "message": "human message",
  "meta": {}
}
```
