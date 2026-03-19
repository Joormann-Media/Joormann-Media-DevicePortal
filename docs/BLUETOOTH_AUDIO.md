# Bluetooth Audio Im Device Portal

## Ziel
Das Portal steuert Bluetooth-Audioausgabe des Raspberry Pi:

- Bluetooth-Scan
- Pair / Connect / Disconnect / Forget
- bekannte Geräte mit Status
- Audio-Output Routing (`local_hdmi`, `local_speaker`, `bluetooth:<MAC>`)

## Architektur

### Backend
- API-Endpunkte in [app/api/routes_network.py](/home/djanebmb/projects/Joormann-Media-JarvisPortal-Rsp/app/api/routes_network.py)
- Kapselung der Systemaufrufe in [app/core/netcontrol.py](/home/djanebmb/projects/Joormann-Media-JarvisPortal-Rsp/app/core/netcontrol.py)
- Systemnahe Helper:
  - [scripts/net/bluetooth_audio.py](/home/djanebmb/projects/Joormann-Media-JarvisPortal-Rsp/scripts/net/bluetooth_audio.py)
  - [scripts/net/audio_output_ctl.py](/home/djanebmb/projects/Joormann-Media-JarvisPortal-Rsp/scripts/net/audio_output_ctl.py)

### Frontend
- BT/AP-Tab mit 4 Cards in [app/templates/index.html](/home/djanebmb/projects/Joormann-Media-JarvisPortal-Rsp/app/templates/index.html)
- AJAX-Flow in [app/static/js/portal.js](/home/djanebmb/projects/Joormann-Media-JarvisPortal-Rsp/app/static/js/portal.js)

## API

- `GET /api/bluetooth/scan?duration=8`
- `GET /api/bluetooth/devices`
- `POST /api/bluetooth/pair`
- `POST /api/bluetooth/connect`
- `POST /api/bluetooth/disconnect`
- `POST /api/bluetooth/forget`
- `GET /api/audio/outputs`
- `POST /api/audio/output`

Request-Beispiel:

```json
{
  "device_id": "AA:BB:CC:DD:EE:FF"
}
```

```json
{
  "output": "bluetooth:AA:BB:CC:DD:EE:FF"
}
```

## Linux-Integration

### Bluetooth
- `bluetoothctl` (BlueZ) für Geräteverwaltung und Aktionen

### Audio Routing
- primär `pactl` (Default Sink setzen + aktive Streams verschieben)
- fallback `wpctl` (falls `pactl` nicht vorhanden)

## Voraussetzungen

Benötigte Pakete/Tools:
- `bluez` / `bluetoothctl`
- `pulseaudio-utils` (`pactl`) oder PipeWire (`wpctl`)

Benötigte Dienste:
- `bluetooth.service`
- Audio-Session mit PulseAudio/PipeWire

## Testablauf

1. Im Tab **BT & AP**: `Scan starten`.
2. Gerät in den Scan-Ergebnissen `Pair` und dann `Verbinden`.
3. In **Bekannte / gekoppelte Geräte** prüfen: `paired`, `connected`, `audio`.
4. In **Audio-Ausgabe / Output Routing** `bluetooth:<MAC>` wählen, `Output setzen`.
5. Im Stream-Tab Audio starten, Ausgabe prüfen.
6. `Trennen` eines BT-Geräts prüfen; Routing sollte wieder auf lokal gesetzt werden können.

## Bekannte Grenzen

- Sink-Erkennung basiert auf üblichen Namensmustern (`bluez_output`, `hdmi`, `analog`).
- Exotische Audio-Setups können manuelle Anpassung der Sink-Mappings erfordern.
- Pairing mit PIN/Passkey bleibt weiterhin im bestehenden Pairing-Assistenten unterstützt; Scan/Action-Flow ist zusätzlich.
