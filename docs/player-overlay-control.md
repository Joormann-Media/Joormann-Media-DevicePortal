# Player Overlay Control (DevicePortal)

## Ziel

Test- und Steueroberfläche für Runtime-Overlays direkt im DevicePortal.
Das Overlay läuft separat vom Stream-Manifest.

## UI

Portal-Route: `/`

Tab-Pfad:
- `System`
- Untertab `Player Overlay`

Dort enthalten:
- Flash-Formular
- Ticker-Formular
- Popup-Formular
- State-Viewer (`overlay-state.json`)
- Clear-/Reset-Aktionen

## API-Routen

- `GET /api/player/overlay/state`
  - Liefert aktuellen Overlay-State + effektiven Dateipfad

- `POST /api/player/overlay/flash`
  - Erstellt/aktualisiert einen Flash-Eintrag (Upsert per `id`)

- `POST /api/player/overlay/ticker`
  - Erstellt/aktualisiert einen Ticker-Eintrag (Upsert per `id`)

- `POST /api/player/overlay/popup`
  - Erstellt/aktualisiert einen Popup-Eintrag (Upsert per `id`)

- `POST /api/player/overlay/clear`
  - Body: `{ "category": "flash|tickers|popups" }`
  - Leert nur die gewünschte Kategorie

- `POST /api/player/overlay/reset`
  - Setzt alle Kategorien zurück

## Dateipfad-Auflösung

Priorität:
1. `OVERLAY_STATE_PATH` oder `DEVICEPLAYER_OVERLAY_STATE_PATH`
2. Aus `var/data/player-source.json` -> `<manifest-dir>/overlay-state.json`
3. Aus Storage-Config Mount -> `<mount>/stream/current/overlay-state.json`
4. Fallback: `var/data/overlay-state.json`

## Datenfluss

1. Formular im Portal absenden
2. Payload serverseitig sanitizen/normalisieren
3. `overlay-state.json` atomar schreiben
4. DevicePlayer erkennt mtime-Änderung
5. Overlay wird ohne Manifest-Änderung aktualisiert

## Zurücksetzen

- Kategorie-spezifisch über `clear`
- Komplett über `reset`
