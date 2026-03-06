# Local Storage/USB Management

## Zweck
Das DevicePortal verwaltet USB-/Storage-Geräte lokal ohne zentrale DB-Anbindung.
Persistenz erfolgt in `var/data/config-storage.json`.

Zusätzlich wird ein interner Medienspeicher als Loop-Datei auf der SD-Karte genutzt:
- Image: `/var/lib/deviceportal/media.img`
- Mountpoint: `/mnt/deviceportal/media`
- Dateisystem: `ext4`
- Größe: `20G`
- Persistenz: `/etc/fstab` Eintrag mit `loop,nofail`

## Wiedererkennung
- Primär: `UUID`
- Fallback: `PARTUUID`
- `/dev/sdX` wird nur als `last_seen_device_path` gespeichert.

## Statusmodell
- `new`: erkannt, noch nicht registriert
- `present`: registriert und angeschlossen
- `mounted`: registriert und eingehängt
- `missing`: registriert, aktuell nicht angeschlossen
- `error`: Mount/Unmount-Fehler im Feld `last_error`

## Config-Datei
Pfad:
- `STORAGE_CONFIG_PATH` (Default: `<PORTAL_DIR>/var/data/config-storage.json`)

Interner Block (`internal`):
- `type = internal_loop`
- `image_path = /var/lib/deviceportal/media.img`
- `mount_path = /mnt/deviceportal/media`
- `filesystem = ext4`
- `size_gb = 20`
- `enabled`, `auto_mount`, `allow_portal_storage`, `allow_media_storage`

Wichtige Felder pro Gerät:
- `id` (`uuid:...` oder `partuuid:...`)
- `uuid`, `part_uuid`, `label`, `filesystem`, `size_bytes`
- `vendor`, `model`, `serial`, `transport`
- `mount_path`, `mount_strategy`, `mount_options`
- `is_enabled`, `auto_mount`
- `allow_portal_storage`, `allow_media_storage`
- `added_at`, `last_seen_at`, `last_seen_device_path`
- `last_known_present`, `last_error`, `notes`

## Verhalten bei Abziehen/Wiederanstecken
- Beim Abziehen bleibt die Konfiguration erhalten (`missing` statt Löschung).
- Beim Wiederanstecken wird das Gerät per UUID/PARTUUID wieder zugeordnet.
- Bei `auto_mount=true` und `is_enabled=true` wird kontrolliert erneut gemountet.

## Mountpoint-Strategie
- Basis: `/mnt/deviceportal/storage`
- Slug aus Label oder UUID/PARTUUID
- Kollisionsarm durch Suffix (`-2`, `-3`, ...)

## Wrapper-Skripte
- `scripts/net/storage_probe.sh` (Erkennung, JSON)
- `scripts/net/storage_mount.sh` (Mount via UUID/PARTUUID)
- `scripts/net/storage_unmount.sh` (Unmount via Mountpfad)

## Setup internes Loop-Storage
Setup-Helfer:
- `install/setup_internal_storage.sh`

Eigenschaften:
- idempotent
- keine SD-Repartitionierung
- keine doppelte fstab-Zeile
- keine Neuformatierung, wenn bereits gültiges ext4 vorhanden
- Freispeicherprüfung vor Datei-Anlage (`20G + Reserve`)

## API-Endpunkte
- `GET /api/network/storage/status`
- `POST /api/network/storage/register`
- `POST /api/network/storage/ignore`
- `POST /api/network/storage/unignore`
- `POST /api/network/storage/remove`
- `POST /api/network/storage/mount`
- `POST /api/network/storage/unmount`
- `POST /api/network/storage/toggle-enabled`
- `POST /api/network/storage/toggle-automount`
- `GET /api/network/storage/file-manager/tree`
- `GET /api/network/storage/file-manager/list`
- `GET /api/network/storage/file-manager/preview`
- `POST /api/network/storage/file-manager/delete`
- `GET /api/network/storage/file-manager/file`
- `POST /api/network/storage/file-manager/upload`

## Integrierter File-Manager (Storage-Tab)
- Einstieg pro registriertem Laufwerk über `Dateien verwalten`.
- 3-Spalten-Ansicht:
  - links: Verzeichnis-Browser inkl. Breadcrumb
  - mitte: Ordner-/Dateiliste inkl. `Select all`, `Unselect all`, `Delete selected`
  - rechts: Upload (Drag&Drop/Auswahl, AJAX-Progress) + Live-Vorschau / Details
- View-Wechsel ohne Reload:
  - Storage-Übersicht slidet aus
  - File-Manager slidet ein
  - `Zurück` stellt die Übersicht wieder her

## Sicherheitslogik im File-Manager
- Zugriff nur auf registrierte Geräte aus `config-storage.json`.
- Zugriff nur auf aktuell gemountete Mountpoints.
- Alle Pfade werden relativ zum zugehörigen Mountpoint aufgelöst.
- Path Traversal (`../`) wird serverseitig blockiert.
- Löschen ist nur innerhalb des Mountpoints erlaubt.
- Root des Laufwerks selbst kann nicht gelöscht werden.
- Symlinks sind im File-Manager strikt blockiert:
  - keine Navigation
  - keine Vorschau
  - keine Löschung über Symlink-Pfade

## Delete-Bestätigung (2-stufig)
- Stufe 1: Auswahl + Klick auf `Delete selected`.
- Stufe 2: Bootstrap-Modal mit Warnung, Anzahl der Einträge und Pflicht-Eingabe `DELETE`.
- Server prüft zusätzlich:
  - `confirm_word == DELETE`
  - `confirm_count` muss exakt zur Anzahl der ausgewählten Pfade passen.

## Preview-Limits
- Text:
  - nur bis max Dateigröße `512 KiB`
  - Response-Ausschnitt max `12.000` Zeichen
- Bilder:
  - nur bis max Dateigröße `8 MiB`
  - optionaler Dimensionscheck (wenn Pillow verfügbar), sonst nur Dateigrößenlimit
- PDF:
  - nur bis max Dateigröße `12 MiB`
- File-Preview-Endpunkt (`/file`):
  - harte Obergrenze `16 MiB`
- Bei Überschreitung: keine Vollvorschau, stattdessen klare Meldung im UI.
