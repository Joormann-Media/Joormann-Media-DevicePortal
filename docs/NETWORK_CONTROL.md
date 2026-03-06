# Network Control

## Abstract
Die Netzwerkkontrolle wird über dedizierte Wrapper-Skripte (`scripts/net/*`) umgesetzt. Flask führt keine freien Shell-Strings aus, sondern ruft nur whitelisted Skripte via `app/core/netcontrol.py` auf.

## Komponenten

- API: `app/api/routes_network.py` (`/api/network/*`)
- Service-Layer: `app/core/netcontrol.py`
- Wrapper-Skripte:
  - `scripts/net/network_info.sh`
  - `scripts/net/wifi_toggle.sh`
  - `scripts/net/wifi_profile.sh`
  - `scripts/net/wifi_status.sh`
  - `scripts/net/wifi_disconnect.sh`
  - `scripts/net/wifi_dhcp.sh`
  - `scripts/net/ap_enable.sh`
  - `scripts/net/ap_disable.sh`
  - `scripts/net/ap_status.sh`
  - `scripts/net/ap_clients.sh`
  - `scripts/net/bluetooth_toggle.sh`
  - `scripts/net/lan_toggle.sh`
  - `scripts/net/wps_start.sh`
  - `scripts/net/tailscale_dns_fix.sh`

## Sicherheitsprinzipien

- Keine direkten Kommandos aus Request-Daten.
- Skriptpfade werden serverseitig auf bekannte Pfade aufgelöst.
- LAN-Interface ist whitelisted (`eth0`).
- WLAN-Interface für WPS ist whitelisted (`wlan0`).
- `subprocess.run(..., timeout=...)` mit `capture_output=True`.
- Keine Ausgabe von WLAN-Passwörtern.

## Deployment-Variante (minimal-invasiv)

Bevorzugt wird `sudoers` mit eingeschränkter NOPASSWD-Liste:

- Installationsscript: `install/setup_netcontrol.sh`
- Deploy-Ziel: `/opt/deviceportal/bin`
- sudoers: `/etc/sudoers.d/deviceportal-net`

Nur folgende Aktionen werden per sudo freigegeben:

- `wifi_toggle.sh`
- `wifi_profile.sh`
- `wifi_status.sh`
- `wifi_disconnect.sh`
- `wifi_dhcp.sh`
- `ap_enable.sh`
- `ap_disable.sh`
- `ap_status.sh`
- `ap_clients.sh`
- `bluetooth_toggle.sh`
- `lan_toggle.sh`
- `wps_start.sh`
- `tailscale_dns_fix.sh`
- `tailscale_dns_fix.sh`

`network_info.sh` läuft ohne sudo.

## API-Endpunkte

- `GET /api/network/info`
- `GET /api/network/wifi/status`
- `POST /api/network/wifi/toggle`
- `POST /api/network/wifi/disconnect`
- `POST /api/network/bluetooth/toggle`
- `POST /api/network/lan/toggle`
- `POST /api/network/wps`
- `POST /api/network/wifi/wps/start`
- `GET /api/network/wifi/wps/status`
- `GET /api/network/ap/status`
- `POST /api/network/ap/toggle`
- `GET /api/network/ap/clients`
- `GET /api/network/wifi/logs`
- `GET /api/wifi/scan`
- `POST /api/wifi/connect`
- `GET /api/wifi/profiles`
- `POST /api/wifi/profiles/add`
- `POST /api/wifi/profiles/delete`
- `POST /api/wifi/profiles/prefer`
- `POST /api/wifi/profiles/up`
- `POST /api/wifi/profiles/apply`
- `POST /api/system/tailscale/disable-dns`

## WPS Schnelltest

```bash
bash -n scripts/net/wps_start.sh
scripts/net/wps_start.sh wlan0 120
curl -s -X POST http://127.0.0.1:5070/api/network/wps | jq
```

## Troubleshooting

1. `sudo: a password is required`
- sudoers nicht installiert oder falscher Service-User (`www-data`) in `setup_netcontrol.sh`.

2. `script_missing`
- Skripte nicht nach `/opt/deviceportal/bin` deployed und Fallback-Pfad nicht verfügbar.

3. `nmcli not found` / `rfkill not found`
- fehlende Pakete; `sudo ./install/setup_netcontrol.sh` erneut ausführen.

4. LAN toggle schlägt fehl
- Interface nicht `eth0` oder Interface auf Zielsystem anders benannt.
