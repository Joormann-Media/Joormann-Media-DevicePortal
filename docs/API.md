# API Reference: Joormann-Media DevicePortal

## Abstract
Vollständige Endpoint-Inventarisierung auf Basis der aktuellen Flask-Blueprints (`routes_status`, `routes_panel`, `routes_plan`, `routes_network`, `routes_ui`).

## Auth-Modell (Ist)
Aktuell ist **keine** Endpoint-Authentisierung implementiert.

## Error-Format (Ist)
Nicht vollständig vereinheitlicht. Typische Patterns:
- `{ "ok": false, "error": "..." }`
- Erweiterte Fehlerobjekte mit Kontext (`panel_link_state`, `panel_response`, `resolved_url`)

## Endpoint-Übersicht

| Group | Method | Path | Handler |
|---|---|---|---|
| public/ui | GET | `/` | `routes_ui.index` |
| status | GET | `/health` | `routes_status.health` |
| status | GET | `/api/status` | `routes_status.api_status` |
| status | GET | `/api/fingerprint` | `routes_status.api_fingerprint` |
| status | POST | `/api/fingerprint/refresh` | `routes_status.api_fingerprint_refresh` |
| status | POST | `/api/status/fingerprint/refresh` | `routes_status.api_status_fingerprint_refresh` |
| status | GET | `/api/state` | `routes_status.api_state` |
| status | GET | `/api/status/state` | `routes_status.api_status_state` |
| panel | POST | `/api/panel/test-url` | `routes_panel.api_panel_test_url` |
| panel | POST | `/api/panel/ping` | `routes_panel.api_panel_ping` |
| panel | POST | `/api/panel/register` | `routes_panel.api_panel_register` |
| panel | GET | `/api/panel/link-status` | `routes_panel.api_panel_link_status` |
| panel | POST | `/api/panel/unlink` | `routes_panel.api_panel_unlink` |
| plan | POST | `/api/plan/pull` | `routes_plan.api_plan_pull` |
| plan | GET | `/api/plan/current` | `routes_plan.api_plan_current` |
| network | GET | `/api/network/info` | `routes_network.api_network_info` |
| network | POST | `/api/network/wifi/toggle` | `routes_network.api_network_wifi_toggle` |
| network | POST | `/api/network/bluetooth/toggle` | `routes_network.api_network_bluetooth_toggle` |
| network | POST | `/api/network/lan/toggle` | `routes_network.api_network_lan_toggle` |
| network | POST | `/api/network/wps` | `routes_network.api_network_wps` |

Quelle der Route-Definitionen:
- [app/api/routes_status.py](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/api/routes_status.py)
- [app/api/routes_panel.py](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/api/routes_panel.py)
- [app/api/routes_plan.py](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/api/routes_plan.py)
- [app/api/routes_network.py](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/api/routes_network.py)
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
  - `state`

**200 example**
```json
{
  "ok": true,
  "config": {"admin_base_url": "https://..."},
  "device": {"device_uuid": "...", "auth_key": "********abcd"},
  "fingerprint": {"hostname": "...", "kernel": "..."},
  "state": {"mode": "setup", "panel": {"linked": false}}
}
```

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

## 11) POST `/api/plan/pull`
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

## 12) GET `/api/plan/current`
- **Auth:** none
- **200:** `{ "ok": true, "plan": {...} }`
- **404:** `{ "ok": false, "error": "plan_missing", "path": "..." }`

---

## 13) GET `/`
- HTML Setup/Diagnostics Oberfläche.
- Nutzt JS `fetch()` auf oben genannte JSON-Endpunkte.

---

## 14) GET `/api/network/info`
- **Auth:** none
- **200:** Netzwerkstatus aus Wrapper-Script (`network_info.sh`)

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
