# DevicePortal UI/Sync Audit (2026-03-10)

## Scope
- Main dashboard: `app/templates/index.html` + `app/static/js/portal.js`
- AP/WiFi setup page: `app/templates/wifi_setup.html` + `app/static/js/wifi_setup.js`
- Focus: Refresh/update triggers, toast/flash messages, admin sync (to/from admin), tab behavior, every button + form field.

## High-level findings
1. There is no custom `tab changed` auto-refresh hook (`shown.bs.tab`) in the dashboard JS.
2. Most live refresh is polling-based and starts on page boot.
3. `refreshStatus()` always calls `refreshPanelFlagsLive()`, which triggers `/api/panel/ping` when linked. This means many UI actions can indirectly ping admin.
4. Toasts are centralized via `toast()` and are used for virtually all action outcomes.
5. Update flow has its own polling and reload logic, including persisted flash message across reload.

## Auto refresh / polling
From `app/static/js/portal.js`:
- Storage polling: every `7000ms` via `startStoragePolling()`.
- AP polling: every `5000ms` via `startApPolling()`.
- WPS polling: every `3000ms` via `startWpsPolling()` (max loops / stop conditions).
- Bluetooth pairing polling: every `900ms` while modal/session active.
- Stream player install/update polling: every `3500ms` while running.
- Portal update polling: every `2000ms` while running.

Boot sequence (`DOMContentLoaded -> boot()`):
- Immediate refresh calls (status/network/wifi/storage/stream/ap/update states) and then starts AP + Storage polling.

## Tab behavior
- Tabs are Bootstrap-based (`data-bs-toggle="tab"` in template).
- No listener for `shown.bs.tab`/`hidden.bs.tab` in `portal.js`.
- Explicit tab switching is done programmatically only for guided flows (e.g. `openUpdateTab()`, setup focus).

## Toast/flash behavior
- Central function: `toast(message, type)` in `portal.js` and similar in `wifi_setup.js`.
- Used for success, warning, danger across all actions.
- Portal update flow persists one flash in `sessionStorage` and shows it after reload (`UPDATE_RESULT_FLASH_KEY`).

## Admin sync and update-related flows
### Pull/check against admin (via portal backend endpoints)
- `/api/panel/test-url`
- `/api/panel/validate-token`
- `/api/panel/register`
- `/api/panel/link-status`
- `/api/panel/ping`
- `/api/panel/sync-status`
- `/api/panel/search-users`
- `/api/panel/search-customers`

### Push/report to admin
- `/api/panel/assign`
- `/api/panel/sync-now`
- `/api/panel/rebuild-fingerprint`
- `/api/plan/pull` (reads plan from admin side)

### Update indicators
- Hero update badge is refreshed from status payload.
- Portal update flow:
  - Start: `/api/system/portal/update`
  - Poll: `/api/system/portal/update/status`
  - On success may trigger admin sync (`trySyncUpdateResultToAdmin()`), then page reload.

## API endpoints called by main dashboard JS
Unique `fetchJson()` targets in `portal.js`:
- `/api/display/config`
- `/api/network/ap/clients`
- `/api/network/ap/status`
- `/api/network/ap/toggle`
- `/api/network/bluetooth/pairing/confirm`
- `/api/network/bluetooth/pairing/reject`
- `/api/network/bluetooth/pairing/start`
- `/api/network/bluetooth/pairing/status`
- `/api/network/bluetooth/pairing/stop`
- `/api/network/bluetooth/toggle`
- `/api/network/info`
- `/api/network/lan/toggle`
- `/api/network/security/sentinels/install`
- `/api/network/security/sentinels/status`
- `/api/network/security/sentinels/uninstall`
- `/api/network/security/sentinels/webhook`
- `/api/network/security/settings`
- `/api/network/security/status`
- `/api/network/security/trust/current`
- `/api/network/security/trust/remove`
- `/api/network/storage/file-manager/delete`
- `/api/network/storage/file-manager/mkdir`
- `/api/network/storage/file-manager/rename`
- `/api/network/storage/format`
- `/api/network/storage/ignore`
- `/api/network/storage/mount`
- `/api/network/storage/register`
- `/api/network/storage/remove`
- `/api/network/storage/status`
- `/api/network/storage/toggle-automount`
- `/api/network/storage/toggle-enabled`
- `/api/network/storage/unignore`
- `/api/network/storage/unmount`
- `/api/network/wifi/logs?limit=120`
- `/api/network/wifi/toggle`
- `/api/network/wifi/wps/start`
- `/api/network/wifi/wps/status`
- `/api/panel/assign`
- `/api/panel/link-status`
- `/api/panel/ping`
- `/api/panel/rebuild-fingerprint`
- `/api/panel/register`
- `/api/panel/sync-now`
- `/api/panel/sync-status`
- `/api/panel/test-url`
- `/api/panel/unlink`
- `/api/panel/validate-token`
- `/api/plan/pull`
- `/api/status`
- `/api/status/fingerprint/refresh`
- `/api/status/state`
- `/api/stream/overview`
- `/api/stream/player/install-update`
- `/api/stream/player/repo`
- `/api/stream/player/status`
- `/api/stream/select`
- `/api/stream/sync`
- `/api/system/hostname/preview`
- `/api/system/hostname/rename`
- `/api/system/portal/restart`
- `/api/system/portal/update`
- `/api/system/power`
- `/api/system/settings`
- `/api/system/tailscale/disable-dns`
- `/api/wifi/connect`
- `/api/wifi/profiles`
- `/api/wifi/profiles/add`
- `/api/wifi/profiles/apply`
- `/api/wifi/profiles/delete`
- `/api/wifi/profiles/prefer`
- `/api/wifi/profiles/up`
- `/api/wifi/scan`

## Main dashboard: all button IDs
From `app/templates/index.html`:
- `btn-ap-disable`
- `btn-ap-enable`
- `btn-ap-refresh`
- `btn-bt-pairing-confirm`
- `btn-bt-pairing-reject`
- `btn-bt-pairing-start`
- `btn-bt-pairing-stop`
- `btn-bt-toggle`
- `btn-confirm-unlink`
- `btn-display-refresh`
- `btn-fix-tailscale-dns`
- `btn-hostname-rename-save`
- `btn-lan-toggle`
- `btn-link-assign`
- `btn-link-rebuild-fingerprint`
- `btn-link-refresh-status`
- `btn-link-register`
- `btn-link-unlink`
- `btn-panel-sync-check`
- `btn-panel-sync-now`
- `btn-player-restart`
- `btn-player-start`
- `btn-player-status-refresh`
- `btn-player-stop`
- `btn-refresh-network`
- `btn-security-ap-disable`
- `btn-security-refresh-live`
- `btn-security-save`
- `btn-security-trust-bt`
- `btn-security-trust-lan`
- `btn-security-trust-wifi`
- `btn-sentinel-refresh`
- `btn-sentinel-webhook-save`
- `btn-status-reboot`
- `btn-status-restart-portal`
- `btn-status-shutdown`
- `btn-storage-delete-confirm`
- `btn-storage-fm-back`
- `btn-storage-fm-delete-selected`
- `btn-storage-fm-new-folder`
- `btn-storage-fm-rename`
- `btn-storage-fm-select-all`
- `btn-storage-fm-unselect-all`
- `btn-storage-modal-automount`
- `btn-storage-modal-enabled`
- `btn-storage-modal-format`
- `btn-storage-modal-mount`
- `btn-storage-modal-remove`
- `btn-storage-modal-unmount`
- `btn-storage-new-folder-confirm`
- `btn-storage-refresh`
- `btn-storage-rename-confirm`
- `btn-stream-player-install-update`
- `btn-stream-player-repo-save`
- `btn-stream-player-update-status`
- `btn-stream-refresh`
- `btn-stream-select-save`
- `btn-stream-sync`
- `btn-system-hostname-modal`
- `btn-system-refresh-network`
- `btn-system-refresh-network-radio`
- `btn-system-storage-security-save`
- `btn-system-update-portal`
- `btn-wifi-logs-refresh`
- `btn-wifi-manual-add`
- `btn-wifi-profiles-apply`
- `btn-wifi-profiles-refresh`
- `btn-wifi-scan`
- `btn-wifi-toggle`
- `btn-wps`
- `hero-update-btn`
- `setup-finish-now`
- `setup-go-step-3`
- `setup-wizard-back`
- `setup-wizard-complete`
- `setup-wizard-next`
- `setup-wizard-skip-complete`
- `status-mode-badge`

## Main dashboard: all form field IDs
From `app/templates/index.html` (`input|select|textarea`):
- `admin-base-url`
- `bt-pairing-timeout-target`
- `device-slug`
- `hostname-rename-confirm`
- `hostname-rename-hardcore`
- `hostname-rename-input`
- `registration-token`
- `security-perimeter-enabled`
- `sentinel-webhook-url`
- `setup-link-search`
- `setup-link-type-customer`
- `setup-link-type-skip`
- `setup-link-type-user`
- `setup-panel-url`
- `setup-registration-token`
- `storage-delete-confirm-word`
- `storage-fm-upload-picker`
- `storage-new-folder-name`
- `storage-rename-new-name`
- `stream-player-repo-dir`
- `stream-player-service-name`
- `stream-player-service-user`
- `stream-select`
- `stream-slug`
- `system-storage-delete-hardcore`
- `wifi-manual-hidden`
- `wifi-manual-password`
- `wifi-manual-ssid`

## AP/WiFi setup page (`/wifi-setup`)
Buttons:
- `btn-wifi-setup-wps-hero`
- `btn-wifi-setup-refresh`
- `btn-wifi-setup-scan`
- `btn-wifi-setup-known-refresh`
- `btn-wifi-setup-radio-on`
- `btn-wifi-setup-radio-off`
- `btn-wifi-setup-reboot`
- `btn-wifi-setup-connect`
- `btn-wifi-setup-wps`
- `btn-wifi-setup-wps-refresh`

Form fields:
- `wifi-setup-manual-ssid`
- `wifi-setup-manual-password`

Called endpoints in `wifi_setup.js`:
- `/api/network/info`
- `/api/network/wifi/logs?limit=120`
- `/api/network/wifi/status`
- `/api/network/wifi/toggle`
- `/api/network/wifi/wps/start`
- `/api/network/wifi/wps/status`
- `/api/system/power`
- `/api/wifi/connect`
- `/api/wifi/profiles`
- `/api/wifi/profiles/delete`
- `/api/wifi/profiles/up`
- `/api/wifi/scan`

## Short risk notes
- Because `refreshStatus()` is called from many flows and includes panel flag refresh, `/api/panel/ping` can be triggered often when linked.
- Polling is distributed (AP/Storage/WPS/BT/update/player-update). If needed, this can be consolidated under visibility-aware polling (only active tab).
