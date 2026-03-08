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
- `GET /api/stream/player/repo`
- `POST /api/stream/player/repo`
  - Player-Repo-Link + Service-Name/User speichern.
  - `player_repo_link` kann URL **oder** lokaler Pfad sein.
- `POST /api/stream/player/install-update`
  - Startet Install/Update-Job für den Player aus dem verlinkten Repo
- `GET /api/stream/player/install-update/status`
  - Jobstatus + Logtail

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
- `scripts/net/player_update.sh`

## Repo-Link Verhalten

- Bei lokalem Pfad:
  - verwendet genau diesen Pfad als Player-Repo.
- Bei URL (`https://...`, `git@...`, `ssh://...`):
  - Portal klont automatisch in den Nachbarordner zum Portal-Repo.
  - Beispiel:
    - Portal: `/home/djanebmb/projects/Joormann-Media-Deviceportal`
    - URL: `https://github.com/Joormann-Media/Joormann-Media-DevicePlayer.git`
    - Ziel: `/home/djanebmb/projects/Joormann-Media-DevicePlayer`

und netcontrol wrapper:

- `player_service_action(...)`

Standard-Service:

- `joormann-media-deviceplayer.service`
