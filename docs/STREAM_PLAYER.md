# Stream + Player Flow

## Ziel

Das Device Portal steuert den lokalen Device Player und synchronisiert Stream-Manifeste/Assets aus dem Adminpanel.

## API im Portal

- `GET /api/stream/overview`
  - Lädt Streamliste vom Admin (`/api/device/link/streams`) mit Device-Credentials.
  - Liefert Auswahlstatus, Storage-Ziel und Player-Status.
- `POST /api/stream/select`
  - Payload: `{ "streamSlug": "..." }`
  - Speichert lokalen ausgewählten Stream in `config.json`.
  - Meldet Auswahl optional ans Adminpanel (`/api/device/link/streams/select`).
- `POST /api/stream/sync`
  - Lädt Manifest vom Admin (`/api/device/link/streams/{slug}/player-manifest`).
  - Lädt Assets lokal.
  - Nutzt staging/current-Strategie atomar.
- `GET /api/stream/player/status`
- `POST /api/stream/player/start|stop|restart`

## Storage-Regel

Sync wird nur ausgeführt, wenn ein Speicher vorhanden ist mit:

- `mounted = true`
- `allow_media_storage = true`

Quelle kann sein:

- interner LoopDrive
- externer USB-Speicher

Es gibt **kein** stilles Fallback auf rootfs.

## Lokaler Dateiaufbau

Unter `<mount>/stream`:

- `staging/build-<timestamp>/manifest.json`
- `staging/build-<timestamp>/assets/*`
- `current/manifest.json`
- `current/assets/*`

Umschaltung:

1. Vollständiger Download nach `staging/build-*`
2. Danach Rename von `current` -> `current.prev.*`
3. Rename von `staging/build-*` -> `current`
4. `current.prev.*` wird gelöscht

## Player-Steuerung

Das Portal nutzt Script:

- `scripts/net/player_service.sh`

und netcontrol wrapper:

- `player_service_action(...)`

Standard-Service:

- `joormann-media-deviceplayer.service`
