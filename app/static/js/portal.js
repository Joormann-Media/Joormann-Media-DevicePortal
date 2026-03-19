(() => {
  const els = {};
  let networkState = null;
  let wifiProfilesState = null;
  let selectedWpsTarget = null;
  let wpsPollHandle = null;
  let apPollHandle = null;
  let btPairingPollHandle = null;
  let btPairingLatest = {};
  let storagePollHandle = null;
  let updatePollHandle = null;
  let currentUpdateJobId = "";
  let streamPlayerUpdateJobId = "";
  let streamPlayerUpdatePollHandle = null;
  let streamAudioVolumeDebounceHandle = null;
  let streamAudioLastNonZeroVolume = 65;
  let streamAudioMuted = false;
  let apClientsInitialized = false;
  let apKnownConnectedMacs = new Set();
  let storageInitialized = false;
  let knownStorageStates = new Map();
  let knownNewStorageIds = new Set();
  let knownStorageById = new Map();
  let knownDrivesById = new Map();
  let selectedStorageDeviceId = "";
  let storageFmPreviewObjectUrl = "";
  let storageDeletePendingPaths = [];
  let storageRenamePendingPath = "";
  const storageFmState = {
    active: false,
    deviceId: "",
    deviceName: "",
    currentPath: "",
    entries: [],
    selectedPaths: new Set(),
    activeEntryPath: "",
    uploadQueue: [],
    uploadRunning: false,
  };
  const portalSecurityState = {
    storageDeleteHardcoreMode: false,
    networkSecurity: {
      enabled: false,
      trusted_wifi: [],
      trusted_lan: [],
      trusted_bluetooth: [],
      assessment: null,
      catalog: null,
    },
    sentinels: {
      webhookUrl: "",
      sourceDir: "",
      sourceError: "",
      configPath: "",
      items: [],
    },
  };
  const statusDashboardState = {
    status: null,
    network: null,
    storage: null,
  };
  const connectivitySetupState = {
    active: false,
    reason: "",
    message: "",
    ap: {},
  };
  let connectivitySetupFocusDone = false;

  const REQUEST_TIMEOUTS = {
    panelUrlTestMs: 15000,
    panelTokenValidateMs: 15000,
    panelRegisterMs: 30000,
    panelSearchMs: 15000,
    panelAssignMs: 15000,
  };
  const panelSyncState = {
    lastCheckAt: "",
  };
  const maintainerState = {
    hydrating: false,
    hydratedOnce: false,
  };
  const hostnameRenameState = {
    preview: null,
    previewTimer: null,
  };
  const setupWizardState = {
    step: 1,
    mode: "setup",
    panelUrl: "",
    token: "",
    verifiedUrl: false,
    registered: false,
    linkType: "skip",
    selectedLinkItem: null,
    searchTimer: null,
    searchSeq: 0,
    existingDetected: false,
  };
  const UPDATE_CACHE_KEY = "deviceportal.portal_update_status.v1";
  const UPDATE_RESULT_FLASH_KEY = "deviceportal.portal_update_result_flash.v1";
  const STORAGE_FM_UPLOAD_MAX_FILE_BYTES = 512 * 1024 * 1024;

  function q(id) {
    return document.getElementById(id);
  }

  function plainText(input) {
    if (typeof input !== "string") return "";
    return input
      .replace(/<[^>]*>/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  async function fetchJson(url, options = {}) {
    const timeoutMs = Number.isFinite(options.timeoutMs) ? Number(options.timeoutMs) : 0;
    const optsInput = { ...options };
    delete optsInput.timeoutMs;

    const opts = {
      headers: {
        Accept: "application/json",
      },
      ...optsInput,
    };
    if (opts.body && typeof opts.body !== "string") {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }

    let timeoutId = null;
    if (timeoutMs > 0 && typeof AbortController !== "undefined") {
      const controller = new AbortController();
      timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
      opts.signal = controller.signal;
    }

    let res;
    let txt = "";
    try {
      res = await fetch(url, opts);
      txt = await res.text();
    } catch (err) {
      if (err && err.name === "AbortError") {
        const seconds = Math.max(1, Math.round(timeoutMs / 1000));
        throw new Error(`Zeitüberschreitung nach ${seconds}s. Panel-Anfrage hat zu lange gedauert.`);
      }
      throw err;
    } finally {
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId);
      }
    }

    let payload;
    try {
      payload = txt ? JSON.parse(txt) : {};
    } catch (_) {
      const preview = plainText(txt).slice(0, 220);
      payload = {
        ok: false,
        error: {
          code: "invalid_json",
          message: `Server returned non-JSON response (HTTP ${res.status})`,
          detail: preview || "No response body",
        },
      };
    }
    if (!res.ok || payload.ok === false) {
      const err = payload.error || {};
      const message = err.message || payload.error || `HTTP ${res.status}`;
      const detail = err.detail || payload.detail || payload.details || "";
      const hint = payload.hint || "";
      const suffix = [detail, hint].filter(Boolean).join(" | ");
      throw new Error(suffix ? `${message} (${suffix})` : message);
    }
    return payload;
  }

  function toast(message, type = "info") {
    const host = els.alertHost;
    const cls = type === "danger" ? "danger" : type === "success" ? "success" : "secondary";
    const div = document.createElement("div");
    div.className = `alert alert-${cls} alert-dismissible fade show py-2 mb-2`;
    div.role = "alert";
    const span = document.createElement("span");
    span.textContent = String(message || "");
    const close = document.createElement("button");
    close.type = "button";
    close.className = "btn-close";
    close.setAttribute("data-bs-dismiss", "alert");
    close.setAttribute("aria-label", "Close");
    div.append(span, close);
    host.prepend(div);
    setTimeout(() => {
      if (div.parentNode) {
        div.classList.remove("show");
        div.remove();
      }
    }, 6000);
  }

  function yn(v) {
    return v ? "yes" : "no";
  }

  function formatSeconds(value) {
    const num = Number(value);
    if (!Number.isFinite(num) || num < 0) return "-";
    return `${Math.round(num)} s`;
  }

  function normalizeOptionalBoolean(value) {
    if (typeof value === "boolean") return value;
    if (typeof value === "number") return value === 1 ? true : value === 0 ? false : null;
    if (typeof value === "string") {
      const raw = value.trim().toLowerCase();
      if (["1", "true", "yes", "on"].includes(raw)) return true;
      if (["0", "false", "no", "off"].includes(raw)) return false;
    }
    return null;
  }

  function clearNode(el) {
    while (el && el.firstChild) {
      el.removeChild(el.firstChild);
    }
  }

  function formatBytes(bytes) {
    const value = Number(bytes || 0);
    if (!Number.isFinite(value) || value < 0) return "-";
    if (value === 0) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let idx = 0;
    let current = value;
    while (current >= 1024 && idx < units.length - 1) {
      current /= 1024;
      idx += 1;
    }
    const fixed = current >= 10 ? current.toFixed(0) : current.toFixed(1);
    return `${fixed} ${units[idx]}`;
  }

  function toPct(value) {
    const num = Number(value || 0);
    if (!Number.isFinite(num)) return 0;
    return Math.max(0, Math.min(100, Math.round(num)));
  }

  function applyConnectivitySetupMode(payload) {
    const setup = payload && typeof payload === "object" ? payload : {};
    const active = !!setup.active;
    connectivitySetupState.active = active;
    connectivitySetupState.reason = String(setup.reason || "");
    connectivitySetupState.message = String(setup.message || "");
    connectivitySetupState.ap = (setup.ap && typeof setup.ap === "object") ? setup.ap : {};

    for (const el of document.querySelectorAll("[data-connectivity-setup-hide='1']")) {
      el.classList.toggle("d-none", active);
    }

    const banner = q("connectivity-setup-banner");
    if (banner) {
      banner.classList.toggle("d-none", !active);
    }

    const inline = q("network-setup-inline");
    if (inline) {
      inline.classList.toggle("d-none", !active);
    }

    const ssid = String(connectivitySetupState.ap.ssid || "").trim() || "jm-hotspot";
    const url = String(connectivitySetupState.ap.portal_url || "").trim() || "http://192.168.4.1";
    const ssidEl = q("connectivity-setup-ssid");
    const urlEl = q("connectivity-setup-url");
    if (ssidEl) ssidEl.textContent = ssid;
    if (urlEl) urlEl.textContent = url;

    const applyBtn = q("btn-wifi-profiles-apply");
    if (applyBtn) {
      applyBtn.classList.toggle("d-none", active);
    }

    if (!active) {
      connectivitySetupFocusDone = false;
      return;
    }

    if (!connectivitySetupFocusDone) {
      const systemTab = q("system-tab");
      const wifiSubTab = q("system-sub-wifi-tab");
      if (systemTab && window.bootstrap && window.bootstrap.Tab) {
        window.bootstrap.Tab.getOrCreateInstance(systemTab).show();
      } else if (systemTab) {
        systemTab.click();
      }
      if (wifiSubTab && window.bootstrap && window.bootstrap.Tab) {
        window.bootstrap.Tab.getOrCreateInstance(wifiSubTab).show();
      } else if (wifiSubTab) {
        wifiSubTab.click();
      }
      connectivitySetupFocusDone = true;
    }
  }

  function updateLocalVersion(update) {
    const readable = String((update || {}).local_version || "").trim();
    if (readable) return readable;
    const shortCommit = String((update || {}).local_commit || "").trim().slice(0, 7);
    return shortCommit || "-";
  }

  function updateRemoteShort(update) {
    const shortCommit = String((update || {}).remote_commit || "").trim().slice(0, 7);
    return shortCommit || "-";
  }

  function playerLocalVersion(update) {
    const readable = String((update || {}).local_version || "").trim();
    if (readable) return readable;
    const shortCommit = String((update || {}).local_commit || "").trim().slice(0, 7);
    return shortCommit || "-";
  }

  function playerRemoteShort(update) {
    const shortCommit = String((update || {}).remote_commit || "").trim().slice(0, 7);
    return shortCommit || "-";
  }

  function getResolvedAdminBaseUrl() {
    const fromInput = String(els.adminBase?.value || "").trim();
    if (fromInput) return fromInput;
    const fromStatus = String(((statusDashboardState.status || {}).config || {}).admin_base_url || "").trim();
    if (fromStatus) return fromStatus;
    return "";
  }

  function requireAdminBaseUrl() {
    const base = getResolvedAdminBaseUrl();
    if (!base) {
      throw new Error("Panel URL fehlt. Bitte zuerst im Setup-Assistenten verknüpfen.");
    }
    return base;
  }

  function setProgressBar(barId, pct, variant = "success") {
    const el = q(barId);
    if (!el) return;
    const width = toPct(pct);
    el.style.width = `${width}%`;
    el.textContent = `${width}%`;
    el.classList.remove("bg-success", "bg-warning", "bg-danger", "bg-info");
    if (variant === "auto") {
      if (width >= 90) el.classList.add("bg-danger");
      else if (width >= 75) el.classList.add("bg-warning");
      else el.classList.add("bg-success");
      return;
    }
    el.classList.add(`bg-${variant}`);
  }

  function renderStatusIdentityCard() {
    const data = statusDashboardState.status || {};
    const state = data.state || {};
    const cfg = data.config || {};
    const panel = state.panel || cfg.panel_link_state || {};
    const dev = data.device || {};
    const fp = data.fingerprint || {};
    const linked = !!panel.linked;

    const linkedBadge = q("status-linked-badge");
    linkedBadge.classList.remove("text-bg-success", "text-bg-warning");
    linkedBadge.classList.add(linked ? "text-bg-success" : "text-bg-warning");
    linkedBadge.textContent = linked ? "LINKED" : "UNLINKED";

    updateModeBadgeInteraction(linked, state.mode || "setup");

    const deviceTypeBadge = q("status-device-type-badge");
    const piSerial = String(dev.pi_serial || "").trim();
    const isPi = !!piSerial && !piSerial.startsWith("unknown");
    deviceTypeBadge.classList.remove("text-bg-secondary", "text-bg-light");
    deviceTypeBadge.classList.add(isPi ? "text-bg-secondary" : "text-bg-light", "border", "text-dark");
    deviceTypeBadge.textContent = isPi ? "Raspberry Pi" : "Dev System";

    const synthesizedFingerprint = [
      String(dev.device_uuid || "").replace(/-/g, "").slice(0, 8),
      String(dev.machine_id || "").slice(0, 8),
      String(fp.hostname || state.hostname || "").slice(0, 12),
    ]
      .filter(Boolean)
      .join("-");

    q("status-fingerprint").textContent = synthesizedFingerprint || "unavailable";
    q("status-fingerprint-updated").textContent = fp.collected_at || "-";
    q("status-device-uuid").textContent = dev.device_uuid || "-";
    q("status-auth-key").textContent = dev.auth_key || "-";
    q("status-pi-serial").textContent = dev.pi_serial || "-";
    q("status-machine-id").textContent = dev.machine_id || "-";
    q("status-cpu-model").textContent = fp.cpu_model || "-";
    q("status-kernel").textContent = fp.kernel || "-";
    q("status-os").textContent = (fp.os || {}).pretty_name || "-";
  }

  function renderStatusHealthCard() {
    const status = statusDashboardState.status || {};
    const network = statusDashboardState.network || {};
    const storage = statusDashboardState.storage || {};
    const fp = status.fingerprint || {};

    const system = status.system || {};
    const load = system.load || {};
    const load1 = Number(load.load_1m || 0);
    const load5 = Number(load.load_5m || 0);
    const cores = Number(load.cpu_cores || 0);
    const cpuPct = Number(load.cpu_percent_estimate || 0);
    let cpuText = "unavailable";
    if (Number.isFinite(load1) && load1 > 0 && Number.isFinite(cores) && cores > 0) {
      const p = Number.isFinite(cpuPct) ? `${Math.round(cpuPct)}%` : "-";
      const l5 = Number.isFinite(load5) ? load5.toFixed(2) : "-";
      cpuText = `${p} (Load: ${load1.toFixed(2)} / ${l5}, ${cores} cores)`;
    } else if (Number.isFinite(cores) && cores > 0) {
      cpuText = `Load: 0.00 (0% / ${cores} cores)`;
    }
    q("status-health-cpu").textContent = cpuText;
    const mem = (system.memory || {});
    const fpMemTotalKb = Number(((fp.memory || {}).mem_total_kb) || 0);
    const totalKb = Number(mem.mem_total_kb || 0) || fpMemTotalKb;
    const availableKb = Number(mem.mem_available_kb || 0);
    const freeKb = Number(mem.mem_free_kb || 0);
    let usedKb = 0;
    let ramText = "unavailable";
    if (totalKb > 0) {
      if (availableKb > 0) {
        usedKb = Math.max(0, totalKb - availableKb);
      } else if (freeKb > 0) {
        usedKb = Math.max(0, totalKb - freeKb);
      } else {
        usedKb = 0;
      }
      const usedPct = totalKb > 0 ? Math.round((usedKb / totalKb) * 100) : 0;
      ramText = `${formatBytes(usedKb * 1024)} / ${formatBytes(totalKb * 1024)} (${usedPct}%)`;
    }
    q("status-health-ram").textContent = ramText;
    const cpuTemp = Number(((system.cpu || {}).temperature_c) || NaN);
    q("status-health-temp").textContent = Number.isFinite(cpuTemp) ? `${cpuTemp.toFixed(1)} °C` : "unavailable";

    let uptimeText = String(system.uptime_human || "").trim();
    if (!uptimeText) {
      const uptimeSeconds = Number(system.uptime_seconds || 0);
      if (Number.isFinite(uptimeSeconds) && uptimeSeconds > 0) {
        const days = Math.floor(uptimeSeconds / 86400);
        const hours = Math.floor((uptimeSeconds % 86400) / 3600);
        const mins = Math.floor((uptimeSeconds % 3600) / 60);
        uptimeText = `${days ? `${days}d ` : ""}${hours}h ${mins}m`.trim();
      }
    }
    q("status-health-uptime").textContent = uptimeText || "unavailable";

    const internal = storage.internal || {};
    const internalTotalBytes = Number(internal.loop_total_bytes || internal.total_bytes || 0);
    const internalUsedBytes = Number(internal.loop_used_bytes || internal.used_bytes || 0);
    const internalPct = toPct(
      internal.loop_used_percent
        ?? (internalTotalBytes > 0 ? (internalUsedBytes / internalTotalBytes) * 100 : 0),
    );
    setProgressBar("status-health-media-progress", internalPct, "auto");
    q("status-health-media-usage").textContent = `${internalPct}%`;
    if (internalTotalBytes > 0) {
      q("status-health-media-meta").textContent = `${formatBytes(internalUsedBytes)} / ${formatBytes(internalTotalBytes)} | ${internal.mount_path || "-"}`;
    } else {
      q("status-health-media-meta").textContent = "keine internen Speicherdaten";
    }

    const drives = Array.isArray(storage.drives) ? storage.drives : [];
    const external = drives.filter((d) => !d.is_internal);
    const externalTotal = external.reduce((sum, item) => sum + Number(item.total_bytes || 0), 0);
    const externalUsed = external.reduce((sum, item) => sum + Number(item.used_bytes || 0), 0);
    const mountedCount = external.filter((item) => !!item.mounted).length;
    const externalPct = externalTotal > 0 ? toPct((externalUsed / externalTotal) * 100) : 0;

    setProgressBar("status-health-external-progress", externalPct, "info");
    q("status-health-external-usage").textContent = externalTotal > 0 ? `${externalPct}%` : "-";
    q("status-health-external-meta").textContent =
      external.length > 0
        ? `${mountedCount}/${external.length} mounted | ${formatBytes(externalUsed)} / ${formatBytes(externalTotal)}`
        : "keine externen Laufwerke";

    const healthBadge = q("status-health-badge");
    healthBadge.classList.remove("text-bg-success", "text-bg-warning", "text-bg-danger", "text-bg-secondary");
    const internalMounted = !!internal.mounted;
    const netConnected = !!(((network.interfaces || {}).wifi || {}).connected || ((network.interfaces || {}).lan || {}).carrier);
    if (internalMounted && netConnected) {
      healthBadge.classList.add("text-bg-success");
      healthBadge.textContent = "healthy";
    } else if (internalMounted || netConnected) {
      healthBadge.classList.add("text-bg-warning");
      healthBadge.textContent = "degraded";
    } else {
      healthBadge.classList.add("text-bg-danger");
      healthBadge.textContent = "warning";
    }
  }

  function renderStatusSoftwareSection() {
    const status = statusDashboardState.status || {};
    const network = statusDashboardState.network || {};
    const storage = statusDashboardState.storage || {};
    const update = status.app_update || {};
    const playerUpdate = status.player_update || {};
    const host = q("status-software-list");
    if (!host) return;
    clearNode(host);

    const tailscale = network.tailscale || {};
    const tailscaleConnected = !!(tailscale.present && tailscale.ip);
    const localVersion = updateLocalVersion(update);
    const remoteShort = updateRemoteShort(update);
    const items = [
      {
        name: "DevicePortal",
        type: "managed",
        version: localVersion,
        state: update.error ? "unknown" : "installed",
        badge: update.error ? "secondary" : "success",
      },
      {
        name: "Portal Update",
        type: "git",
        version: update.available ? `${localVersion} -> ${remoteShort}` : localVersion,
        state: update.available ? "update available" : (update.error ? "check failed" : "up to date"),
        badge: update.available ? "warning" : (update.error ? "secondary" : "success"),
      },
      {
        name: "DevicePlayer",
        type: "git",
        version: playerUpdate.available ? `${playerLocalVersion(playerUpdate)} -> ${playerRemoteShort(playerUpdate)}` : playerLocalVersion(playerUpdate),
        state: playerUpdate.available ? "update available" : (playerUpdate.error ? "check failed" : "installed"),
        badge: playerUpdate.available ? "warning" : (playerUpdate.error ? "secondary" : "success"),
      },
      {
        name: "Tailscale",
        type: "apt",
        version: tailscale.ip || "-",
        state: tailscaleConnected ? "connected" : (tailscale.present ? "installed" : "missing"),
        badge: tailscaleConnected ? "success" : (tailscale.present ? "warning" : "secondary"),
      },
      {
        name: "Storage Service",
        type: "managed",
        version: `${Number(storage.known_count || 0)} known`,
        state: Number(storage.known_count || 0) > 0 ? "active" : "idle",
        badge: Number(storage.known_count || 0) > 0 ? "success" : "secondary",
      },
    ];

    for (const item of items) {
      const card = document.createElement("div");
      card.className = "status-software-item";
      const top = document.createElement("div");
      top.className = "status-software-top";
      const name = document.createElement("div");
      name.className = "status-software-name";
      name.textContent = item.name;
      const badge = document.createElement("span");
      badge.className = `badge text-bg-${item.badge}`;
      badge.textContent = item.state;
      top.append(name, badge);

      const meta = document.createElement("div");
      meta.className = "status-software-meta";
      meta.textContent = `Type: ${item.type} | Version: ${item.version || "-"}`;
      card.append(top, meta);
      host.append(card);
    }

    const countBadge = q("status-software-count");
    if (countBadge) {
      countBadge.textContent = `${items.length} Komponenten`;
    }
  }

  function mountOrientationLabel(value) {
    const v = String(value || "").trim();
    if (v === "landscape_cable_bottom") return "Landscape (Kabel unten)";
    if (v === "landscape_cable_top") return "Landscape (Kabel oben)";
    if (v === "portrait_cable_left") return "Portrait (Kabel links)";
    if (v === "portrait_cable_right") return "Portrait (Kabel rechts)";
    if (v === "custom") return "Custom";
    return "Unknown";
  }

  function renderStatusDisplayQuickSummary() {
    const status = statusDashboardState.status || {};
    const display = status.display || {};
    const displays = Array.isArray(display.displays) ? display.displays : [];
    const quick = q("status-display-quick");
    if (!quick) return;

    if (displays.length === 0) {
      quick.textContent = "0 | -";
      return;
    }

    const primary =
      displays.find((item) => !!item.connected) ||
      displays.find((item) => String(item.status || "").toLowerCase() === "connected") ||
      displays[0];
    const displayName = String(primary?.display_name || primary?.model || "Display").trim();
    const connector = String(primary?.connector || "-").trim();
    const state = String(primary?.status || (primary?.connected ? "connected" : "unknown")).trim();
    quick.textContent = `${displays.length} | ${displayName} ${connector} | ${state}`;
  }

  function renderStatusDisplaySection() {
    const status = statusDashboardState.status || {};
    const display = status.display || {};
    const displays = Array.isArray(display.displays) ? display.displays : [];
    const host = q("status-display-list");
    if (!host) return;
    clearNode(host);

    const countBadge = q("status-display-count");
    if (countBadge) {
      countBadge.textContent = `${displays.length} erkannt`;
    }
    renderStatusDisplayQuickSummary();

    if (displays.length === 0) {
      const empty = document.createElement("div");
      empty.className = "text-secondary small";
      empty.textContent = "Keine Displays erkannt.";
      host.append(empty);
      return;
    }

    const orientationOptions = [
      ["landscape_cable_bottom", "Landscape (Kabel unten)"],
      ["landscape_cable_top", "Landscape (Kabel oben)"],
      ["portrait_cable_left", "Portrait (Kabel links)"],
      ["portrait_cable_right", "Portrait (Kabel rechts)"],
      ["unknown", "Unknown"],
      ["custom", "Custom"],
    ];

    for (const item of displays) {
      const connector = String(item.connector || "").trim();
      const card = document.createElement("div");
      card.className = "status-display-item";

      const head = document.createElement("div");
      head.className = "status-display-head";
      const nameWrap = document.createElement("div");
      const name = document.createElement("div");
      name.className = "status-display-name";
      name.textContent = item.display_name || connector || "Display";
      const meta = document.createElement("div");
      meta.className = "status-display-meta";
      meta.textContent = `${connector || "-"} | ${item.status || "unknown"}`;
      nameWrap.append(name, meta);
      const stateBadge = document.createElement("span");
      stateBadge.className = `badge text-bg-${item.connected ? "success" : "secondary"}`;
      stateBadge.textContent = item.connected ? "connected" : "disconnected";
      head.append(nameWrap, stateBadge);

      const kv = document.createElement("dl");
      kv.className = "status-display-kv";
      const rows = [
        ["Hersteller/Modell", [item.manufacturer_name, item.model].filter(Boolean).join(" ") || "-"],
        ["Auflösung", item.current_mode || "-"],
        ["Refresh", item.current_refresh_hz ? `${item.current_refresh_hz} Hz` : "-"],
        ["Preferred", item.preferred_mode || "-"],
        ["Größe", item.diagonal_inch ? `${item.diagonal_inch}" (${item.physical_width_mm || 0}x${item.physical_height_mm || 0} mm)` : "-"],
        ["EDID", item.edid_available ? "ja" : "nein"],
        ["Montage", mountOrientationLabel(item.mount_orientation)],
        ["Rotation", `${item.rotation_degrees || 0}° (${item.content_orientation || "landscape"})`],
      ];
      for (const [label, value] of rows) {
        const dt = document.createElement("dt");
        dt.textContent = label;
        const dd = document.createElement("dd");
        dd.textContent = String(value || "-");
        kv.append(dt, dd);
      }

      const actions = document.createElement("div");
      actions.className = "status-display-actions";
      const row = document.createElement("div");
      row.className = "row g-2";

      const colSelect = document.createElement("div");
      colSelect.className = "col-md-7";
      const select = document.createElement("select");
      select.className = "form-select form-select-sm";
      for (const [value, label] of orientationOptions) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        if (String(item.mount_orientation || "") === value) {
          option.selected = true;
        }
        select.append(option);
      }
      colSelect.append(select);

      const colToggle = document.createElement("div");
      colToggle.className = "col-md-3 d-flex align-items-center";
      const checkWrap = document.createElement("div");
      checkWrap.className = "form-check";
      const checkbox = document.createElement("input");
      checkbox.className = "form-check-input";
      checkbox.type = "checkbox";
      checkbox.checked = !!item.active;
      const checkLabel = document.createElement("label");
      checkLabel.className = "form-check-label small";
      checkLabel.textContent = "Aktiv";
      checkWrap.append(checkbox, checkLabel);
      colToggle.append(checkWrap);

      const colSave = document.createElement("div");
      colSave.className = "col-md-2 d-grid";
      const saveBtn = document.createElement("button");
      saveBtn.className = "btn btn-outline-primary btn-sm";
      saveBtn.textContent = "Speichern";
      saveBtn.disabled = !connector;
      saveBtn.addEventListener("click", () => {
        run(async () => {
          await saveDisplayConfig(connector, select.value, checkbox.checked);
        });
      });
      colSave.append(saveBtn);

      row.append(colSelect, colToggle, colSave);
      actions.append(row);

      card.append(head, kv, actions);
      host.append(card);
    }
  }

  async function saveDisplayConfig(connector, mountOrientation, active) {
    await fetchJson("/api/display/config", {
      method: "POST",
      body: {
        connector: String(connector || ""),
        mount_orientation: String(mountOrientation || ""),
        active: !!active,
      },
    });
    await refreshStatus();
    try {
      const state = (statusDashboardState.status || {}).state || {};
      const panel = state.panel || {};
      const linked = !!panel.linked;
      const adminBase = String(els.adminBase?.value || "").trim();
      if (linked && adminBase) {
        await panelSyncNow();
      }
    } catch (_) {
      // display settings are persisted locally even if panel sync fails
    }
    toast("Display-Konfiguration gespeichert", "success");
  }

  function setWpsTarget(target) {
    if (target && target.ssid) {
      selectedWpsTarget = {
        ssid: String(target.ssid || "").trim(),
        bssid: String(target.bssid || "").trim(),
      };
    } else {
      selectedWpsTarget = null;
    }
    const label = q("wifi-wps-target");
    if (!label) return;
    if (!selectedWpsTarget) {
      label.textContent = "WPS Ziel: automatisch (kein Netz ausgewählt)";
      return;
    }
    const bssidText = selectedWpsTarget.bssid ? ` / ${selectedWpsTarget.bssid}` : "";
    label.textContent = `WPS Ziel: ${selectedWpsTarget.ssid}${bssidText}`;
  }

  function setStatusBadge(linked, online) {
    const badge = els.heroStatus;
    badge.classList.remove("text-bg-success", "text-bg-warning", "text-bg-danger");
    if (!online) {
      badge.classList.add("text-bg-danger");
      badge.textContent = "OFFLINE";
      return;
    }
    if (linked) {
      badge.classList.add("text-bg-success");
      badge.textContent = "ONLINE / LINKED";
      return;
    }
    badge.classList.add("text-bg-warning");
    badge.textContent = "ONLINE / UNLINKED";
  }

  function renderHeroPanelFlags(config) {
    const flags = (config && typeof config === "object" && config.panel_device_flags && typeof config.panel_device_flags === "object")
      ? config.panel_device_flags
      : {};

    const renderSwitch = (key, wrapId, badgeId, valueId, onText, offText, trueClass, falseClass) => {
      const wrap = q(wrapId);
      const badge = q(badgeId);
      const valueEl = q(valueId);
      if (!wrap || !badge || !valueEl) return;
      const value = normalizeOptionalBoolean(flags[key]);
      wrap.classList.toggle("is-unknown", value === null);
      valueEl.textContent = value === null ? "unknown" : (value ? onText : offText);
      badge.classList.remove("text-bg-success", "text-bg-danger", "text-bg-warning", "text-bg-secondary");
      if (value === null) {
        badge.classList.add("text-bg-secondary");
        badge.textContent = "unknown";
      } else if (value) {
        badge.classList.add(trueClass);
        badge.textContent = onText;
      } else {
        badge.classList.add(falseClass);
        badge.textContent = offText;
      }
    };

    renderSwitch("is_active", "hero-flag-active-wrap", "hero-flag-active-badge", "hero-flag-active-value", "active", "inactive", "text-bg-success", "text-bg-warning");
    renderSwitch("is_locked", "hero-flag-locked-wrap", "hero-flag-locked-badge", "hero-flag-locked-value", "locked", "unlocked", "text-bg-danger", "text-bg-success");

    const updatedEl = q("hero-flag-updated");
    if (updatedEl) {
      updatedEl.textContent = String(flags.updated_at || "-");
    }
  }

  function getSetupLinkType() {
    const checked = document.querySelector('input[name="setup-link-type"]:checked');
    return checked ? String(checked.value || "skip") : "skip";
  }

  function setSetupError(message = "") {
    const box = q("setup-wizard-error");
    if (!box) return;
    const msg = String(message || "").trim();
    if (!msg) {
      box.classList.add("d-none");
      box.textContent = "";
      return;
    }
    box.classList.remove("d-none");
    box.textContent = msg;
  }

  function updateSetupStepDots() {
    for (let i = 1; i <= 3; i += 1) {
      const dot = q(`setup-step-dot-${i}`);
      if (!dot) continue;
      dot.classList.remove("active", "done");
      if (i < setupWizardState.step) dot.classList.add("done");
      else if (i === setupWizardState.step) dot.classList.add("active");
    }
  }

  function updateSetupPanels() {
    for (let i = 1; i <= 3; i += 1) {
      const panel = q(`setup-step-${i}`);
      if (!panel) continue;
      panel.classList.toggle("d-none", i !== setupWizardState.step);
    }
  }

  function updateSetupFooterButtons() {
    const back = q("setup-wizard-back");
    const next = q("setup-wizard-next");
    const complete = q("setup-wizard-complete");
    const skipComplete = q("setup-wizard-skip-complete");
    const step2Actions = q("setup-step-2-success-actions");
    if (!back || !next || !complete || !skipComplete || !step2Actions) return;

    back.disabled = setupWizardState.step <= 1;
    next.classList.add("d-none");
    complete.classList.add("d-none");
    skipComplete.classList.add("d-none");

    if (setupWizardState.step === 1) {
      next.classList.remove("d-none");
      next.textContent = "Weiter";
    } else if (setupWizardState.step === 2) {
      if (!setupWizardState.registered) {
        next.classList.remove("d-none");
        next.textContent = "Jetzt verknüpfen";
      }
      step2Actions.classList.toggle("d-none", !setupWizardState.registered);
    } else if (setupWizardState.step === 3) {
      complete.classList.remove("d-none");
      if (setupWizardState.linkType === "skip") {
        complete.classList.add("d-none");
      }
      skipComplete.classList.remove("d-none");
      skipComplete.textContent = setupWizardState.linkType === "skip" ? "Fertig" : "Ohne Zuordnung beenden";
    }
  }

  function updateSetupSearchUi() {
    const wrap = q("setup-link-search-wrap");
    if (!wrap) return;
    const showSearch = setupWizardState.linkType === "user" || setupWizardState.linkType === "customer";
    wrap.classList.toggle("d-none", !showSearch);
  }

  function renderSetupSearchResults(items = []) {
    const host = q("setup-link-search-results");
    if (!host) return;
    clearNode(host);
    if (!Array.isArray(items) || !items.length) {
      const empty = document.createElement("div");
      empty.className = "text-secondary small";
      empty.textContent = "Keine Treffer.";
      host.append(empty);
      return;
    }
    for (const item of items) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "list-group-item list-group-item-action";
      const subtitle = item.subtitle ? `<div class="small text-secondary">${escapeHtml(item.subtitle)}</div>` : "";
      btn.innerHTML = `<div class="fw-semibold">${escapeHtml(item.name || item.id || "-")}</div>${subtitle}`;
      btn.addEventListener("click", () => {
        setupWizardState.selectedLinkItem = item;
        const sel = q("setup-link-selection");
        if (sel) {
          const kind = setupWizardState.linkType === "user" ? "User" : "Customer";
          sel.textContent = `${kind} ausgewählt: ${item.name} (${item.id})`;
        }
      });
      host.append(btn);
    }
  }

  function setSetupBusy(isBusy) {
    const ids = [
      "setup-wizard-back",
      "setup-wizard-next",
      "setup-wizard-complete",
      "setup-wizard-skip-complete",
      "setup-finish-now",
      "setup-go-step-3",
    ];
    for (const id of ids) {
      const el = q(id);
      if (el) el.disabled = !!isBusy;
    }
  }

  function resetSetupWizard(assignOnly = false) {
    const cfg = (statusDashboardState.status || {}).config || {};
    setupWizardState.mode = assignOnly ? "assign" : "setup";
    setupWizardState.step = assignOnly ? 3 : 1;
    setupWizardState.panelUrl = String(cfg.admin_base_url || "").trim();
    setupWizardState.token = String(cfg.registration_token || "").trim();
    setupWizardState.verifiedUrl = assignOnly;
    setupWizardState.registered = assignOnly;
    setupWizardState.linkType = "skip";
    setupWizardState.selectedLinkItem = null;
    setupWizardState.searchSeq = 0;
    setupWizardState.existingDetected = false;
    if (setupWizardState.searchTimer) {
      window.clearTimeout(setupWizardState.searchTimer);
      setupWizardState.searchTimer = null;
    }
    q("setup-panel-url").value = setupWizardState.panelUrl;
    q("setup-registration-token").value = setupWizardState.token;
    q("setup-step-1-result").textContent = "Noch nicht geprüft.";
    q("setup-step-2-result").textContent = assignOnly ? "Bereits verknüpft. Optionale Zuordnung möglich." : "Noch nicht verknüpft.";
    q("setup-link-search").value = "";
    q("setup-link-search-status").textContent = "Mindestens 2 Zeichen eingeben.";
    q("setup-link-selection").textContent = "Keine Auswahl.";
    renderSetupSearchResults([]);
    q("setup-link-type-skip").checked = true;
    updateSetupSearchUi();
    setSetupError("");
    updateSetupStepDots();
    updateSetupPanels();
    updateSetupFooterButtons();
  }

  function openSetupWizard(mode = "setup") {
    resetSetupWizard(mode === "assign");
    const modal = bootstrap.Modal.getOrCreateInstance(q("setupWizardModal"));
    modal.show();
  }

  async function wizardCheckPanelUrl() {
    setupWizardState.panelUrl = String(q("setup-panel-url").value || "").trim();
    if (!setupWizardState.panelUrl) {
      throw new Error("Bitte eine Panel-URL eingeben.");
    }
    if (!/^https?:\/\//i.test(setupWizardState.panelUrl) && !/^[a-z0-9.-]+(?::\d+)?$/i.test(setupWizardState.panelUrl)) {
      throw new Error("Panel-URL ist ungültig.");
    }
    const payload = await fetchJson("/api/panel/test-url", {
      method: "POST",
      body: { url: setupWizardState.panelUrl },
      timeoutMs: REQUEST_TIMEOUTS.panelUrlTestMs,
    });
    setupWizardState.verifiedUrl = true;
    const result = q("setup-step-1-result");
    const existing = payload && typeof payload.existing_device === "object" ? payload.existing_device : {};
    const existingFound = !!existing.found;
    setupWizardState.existingDetected = existingFound;
    if (result) {
      const h = payload.handshake_http || "-";
      result.textContent = `URL validiert. Handshake erreichbar (HTTP ${h}).`;
    }
    if (els.adminBase) els.adminBase.value = setupWizardState.panelUrl;
    if (existingFound) {
      const slug = String(existing.device_slug || "").trim();
      const question = slug
        ? `Daten gefunden (Device: ${slug}). Direkt verbinden ohne Token?`
        : "Daten gefunden. Direkt verbinden ohne Token?";
      const connectNow = window.confirm(question);
      if (connectNow) {
        setupWizardState.registered = true;
        setupWizardState.step = 3;
        if (result) result.textContent = "Bestehendes Gerät erkannt und übernommen. Token-Schritt übersprungen.";
        await refreshStatus();
        return;
      }
      if (result) {
        result.textContent = "Bestehendes Gerät erkannt, aber nicht übernommen. Weiter mit Token in Schritt 2.";
      }
    }
    if (setupWizardState.step < 2) setupWizardState.step = 2;
  }

  async function wizardRegisterWithToken() {
    setupWizardState.token = String(q("setup-registration-token").value || "").trim();
    if (!setupWizardState.token) {
      throw new Error("Bitte einen Registrierungstoken eingeben.");
    }
    const validatePayload = await fetchJson("/api/panel/validate-token", {
      method: "POST",
      body: {
        admin_base_url: setupWizardState.panelUrl || q("setup-panel-url").value || "",
        registration_token: setupWizardState.token,
      },
      timeoutMs: REQUEST_TIMEOUTS.panelTokenValidateMs,
    });
    if (!validatePayload.valid) {
      throw new Error("Token ist ungültig.");
    }
    const registerPayload = await fetchJson("/api/panel/register", {
      method: "POST",
      body: {
        admin_base_url: setupWizardState.panelUrl || q("setup-panel-url").value || "",
        registration_token: setupWizardState.token,
      },
      timeoutMs: REQUEST_TIMEOUTS.panelRegisterMs,
    });
    setupWizardState.registered = !!registerPayload.ok;
    if (els.regToken) els.regToken.value = setupWizardState.token;
    const result = q("setup-step-2-result");
    if (result) {
      const h = registerPayload.http || "-";
      result.textContent = `Gerät erfolgreich verknüpft (HTTP ${h}).`;
    }
    await refreshStatus();
  }

  async function wizardSearchLinks(query) {
    const qv = String(query || "").trim();
    const statusEl = q("setup-link-search-status");
    if (qv.length < 2) {
      renderSetupSearchResults([]);
      if (statusEl) statusEl.textContent = "Mindestens 2 Zeichen eingeben.";
      return;
    }
    const target = setupWizardState.linkType === "customer" ? "customers" : "users";
    const seq = ++setupWizardState.searchSeq;
    if (statusEl) statusEl.textContent = "Suche läuft…";
    const params = new URLSearchParams({
      q: qv,
      registration_token: setupWizardState.token,
      admin_base_url: setupWizardState.panelUrl,
    });
    const payload = await fetchJson(`/api/panel/search-${target}?${params.toString()}`, {
      cache: "no-store",
      timeoutMs: REQUEST_TIMEOUTS.panelSearchMs,
    });
    if (seq !== setupWizardState.searchSeq) return;
    const items = Array.isArray(payload.items) ? payload.items : [];
    renderSetupSearchResults(items);
    if (statusEl) statusEl.textContent = items.length ? `${items.length} Treffer` : "Keine Treffer";
  }

  async function wizardAssignSelection() {
    if (!setupWizardState.registered) {
      throw new Error("Gerät ist noch nicht verknüpft.");
    }
    if (setupWizardState.linkType === "skip") {
      return;
    }
    if (!setupWizardState.selectedLinkItem || !setupWizardState.selectedLinkItem.id) {
      throw new Error("Bitte erst einen Eintrag auswählen.");
    }
    await fetchJson("/api/panel/assign", {
      method: "POST",
      body: {
        admin_base_url: setupWizardState.panelUrl,
        registration_token: setupWizardState.token,
        target_type: setupWizardState.linkType,
        target_id: setupWizardState.selectedLinkItem.id,
        selected_user: setupWizardState.linkType === "user" ? setupWizardState.selectedLinkItem : null,
        selected_customer: setupWizardState.linkType === "customer" ? setupWizardState.selectedLinkItem : null,
      },
      timeoutMs: REQUEST_TIMEOUTS.panelAssignMs,
    });
  }

  async function completeSetupWizard(closeOnly = false) {
    const modalEl = q("setupWizardModal");
    const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    modal.hide();
    await refreshStatus();
    if (!closeOnly) {
      if (setupWizardState.mode === "assign") {
        toast("Zuordnung erfolgreich gespeichert.", "success");
      } else {
        toast("Gerät erfolgreich mit dem Panel verknüpft.", "success");
      }
    }
  }

  async function wizardNext() {
    setSetupError("");
    setSetupBusy(true);
    try {
      if (setupWizardState.step === 1) {
        await wizardCheckPanelUrl();
      } else if (setupWizardState.step === 2) {
        await wizardRegisterWithToken();
      }
    } catch (err) {
      setSetupError(err && err.message ? err.message : String(err || "Unbekannter Fehler"));
    } finally {
      setSetupBusy(false);
      updateSetupStepDots();
      updateSetupPanels();
      updateSetupFooterButtons();
    }
  }

  function wizardBack() {
    if (setupWizardState.step <= 1) return;
    setupWizardState.step -= 1;
    setSetupError("");
    updateSetupStepDots();
    updateSetupPanels();
    updateSetupFooterButtons();
  }

  function updateModeBadgeInteraction(linked, modeRaw) {
    const modeBadge = q("status-mode-badge");
    if (!modeBadge) return;
    const mode = String(modeRaw || "setup").toUpperCase();
    modeBadge.textContent = mode;
    modeBadge.classList.remove("text-bg-light", "text-bg-success", "text-bg-warning", "text-dark");
    if (linked) {
      modeBadge.classList.add("text-bg-success");
      modeBadge.disabled = true;
      modeBadge.title = "Gerät ist bereits verknüpft";
    } else {
      modeBadge.classList.add("text-bg-warning", "text-dark");
      modeBadge.disabled = false;
      modeBadge.title = "Setup-Assistent öffnen";
    }
  }

  function renderStatus(data) {
    const state = data.state || {};
    const cfg = data.config || {};
    const panel = (state.panel || cfg.panel_link_state || {});
    const linked = !!panel.linked;
    const online = !!state.hostname;
    const update = data.app_update || {};
    const playerUpdate = data.player_update || {};
    statusDashboardState.status = data;

    setStatusBadge(linked, online);
    renderHeroPanelFlags(cfg);
    const updateBadge = q("hero-update");
    if (updateBadge) {
      updateBadge.classList.remove("text-bg-danger", "text-bg-secondary", "text-bg-warning", "text-bg-success");
      const localVersion = updateLocalVersion(update);
      if (update.available) {
        updateBadge.classList.add("text-bg-warning");
        const shortRemote = updateRemoteShort(update);
        updateBadge.textContent = `Update verfügbar (${localVersion} -> ${shortRemote})`;
      } else if (update.error) {
        updateBadge.classList.add("text-bg-secondary");
        updateBadge.textContent = "Update-Check nicht verfügbar";
      } else {
        updateBadge.classList.add("text-bg-success");
        updateBadge.textContent = `Up to date (${localVersion})`;
      }
    }
    const playerBadge = q("hero-update-player");
    if (playerBadge) {
      playerBadge.classList.remove("text-bg-danger", "text-bg-secondary", "text-bg-warning", "text-bg-success");
      const localVersion = playerLocalVersion(playerUpdate);
      if (playerUpdate.available) {
        playerBadge.classList.add("text-bg-warning");
        playerBadge.textContent = `Player Update verfügbar (${localVersion} -> ${playerRemoteShort(playerUpdate)})`;
      } else if (playerUpdate.error) {
        playerBadge.classList.add("text-bg-secondary");
        playerBadge.textContent = "Player-Check nicht verfügbar";
      } else {
        playerBadge.classList.add("text-bg-success");
        playerBadge.textContent = `Player up to date (${localVersion})`;
      }
    }

    q("status-hostname").textContent = state.hostname || "-";
    q("system-hostname-current").textContent = state.hostname || "-";
    q("status-ip").textContent = state.ip || "-";
    q("status-linked").textContent = yn(linked);
    q("status-panel-url").textContent = cfg.admin_base_url || "-";
    q("status-device-slug").textContent = state.device_slug || cfg.device_slug || "-";
    q("status-stream-slug").textContent = state.selected_stream_slug || cfg.selected_stream_slug || "-";
    q("status-last-check").textContent = panel.last_check || "-";
    q("status-last-error").textContent = panel.last_error || "-";
    renderPanelApiKeyStatus(cfg);
    const maintainers = cfg.panel_linked_users || [];
    renderLinkedMaintainers(maintainers);
    ensureLinkedMaintainersHydrated(maintainers, linked);
    renderStatusIdentityCard();
    renderStatusHealthCard();
    renderStatusSoftwareSection();
    renderStatusDisplaySection();

    els.adminBase.value = cfg.admin_base_url || "";
    els.regToken.value = cfg.registration_token || "";
    els.deviceSlug.value = cfg.device_slug || "";
    els.streamSlug.value = cfg.selected_stream_slug || "";
    updateLinkActionButtons(linked);

    portalSecurityState.storageDeleteHardcoreMode = !!cfg.storage_delete_hardcore_mode;
    const hardcoreToggle = q("system-storage-delete-hardcore");
    const hardcoreStatus = q("system-storage-security-status");
    if (hardcoreToggle) {
      hardcoreToggle.checked = portalSecurityState.storageDeleteHardcoreMode;
    }
    if (hardcoreStatus) {
      hardcoreStatus.classList.remove("text-bg-success", "text-bg-secondary");
      if (portalSecurityState.storageDeleteHardcoreMode) {
        hardcoreStatus.classList.add("text-bg-success");
        hardcoreStatus.textContent = "aktiv";
      } else {
        hardcoreStatus.classList.add("text-bg-secondary");
        hardcoreStatus.textContent = "inaktiv";
      }
    }

    const rawNetworkSecurity = (cfg && cfg.network_security && typeof cfg.network_security === "object") ? cfg.network_security : {};
    portalSecurityState.networkSecurity.enabled = !!rawNetworkSecurity.enabled;
    portalSecurityState.networkSecurity.trusted_wifi = Array.isArray(rawNetworkSecurity.trusted_wifi) ? rawNetworkSecurity.trusted_wifi : [];
    portalSecurityState.networkSecurity.trusted_lan = Array.isArray(rawNetworkSecurity.trusted_lan) ? rawNetworkSecurity.trusted_lan : [];
    portalSecurityState.networkSecurity.trusted_bluetooth = Array.isArray(rawNetworkSecurity.trusted_bluetooth) ? rawNetworkSecurity.trusted_bluetooth : [];
    portalSecurityState.networkSecurity.catalog = null;
    const perimeterToggle = q("security-perimeter-enabled");
    if (perimeterToggle) {
      perimeterToggle.checked = portalSecurityState.networkSecurity.enabled;
    }
    renderNetworkSecurityUi();

    const rawSentinel = (cfg && cfg.sentinel_settings && typeof cfg.sentinel_settings === "object") ? cfg.sentinel_settings : {};
    portalSecurityState.sentinels.webhookUrl = String(rawSentinel.webhook_url || "");
    const webhookInput = q("sentinel-webhook-url");
    if (webhookInput && !webhookInput.value) {
      webhookInput.value = portalSecurityState.sentinels.webhookUrl;
    }
  }

  function renderPanelApiKeyStatus(cfg) {
    const keyState = (cfg && typeof cfg === "object" && cfg.panel_api_keys && typeof cfg.panel_api_keys === "object") ? cfg.panel_api_keys : {};
    const bootstrap = (cfg && typeof cfg === "object" && cfg.panel_api_key_bootstrap && typeof cfg.panel_api_key_bootstrap === "object") ? cfg.panel_api_key_bootstrap : {};

    const raspiAdminConfigured = !!(keyState.raspi_to_admin_configured || String(keyState.raspi_to_admin || "").trim());
    const adminRaspiConfigured = !!(keyState.admin_to_raspi_configured || String(keyState.admin_to_raspi || "").trim());
    q("panel-api-key-raspi-admin").textContent = raspiAdminConfigured ? "konfiguriert" : "nicht gesetzt";
    q("panel-api-key-admin-raspi").textContent = adminRaspiConfigured ? "konfiguriert" : "nicht gesetzt";

    const mode = String(bootstrap.mode || "none");
    const status = String(bootstrap.status || "none");
    const pullAt = bootstrap.last_pull_at ? ` | pull: ${bootstrap.last_pull_at}` : "";
    const ackAt = bootstrap.last_ack_at ? ` | ack: ${bootstrap.last_ack_at}` : "";
    q("panel-api-key-bootstrap-status").textContent = `${status} (${mode})${pullAt}${ackAt}`;
  }

  function updateLinkActionButtons(linked) {
    const btnRegister = q("btn-link-register");
    const btnAssign = q("btn-link-assign");
    const btnUnlink = q("btn-link-unlink");
    if (btnRegister) {
      btnRegister.classList.toggle("d-none", !!linked);
    }
    if (btnAssign) {
      btnAssign.classList.toggle("d-none", !linked);
    }
    if (btnUnlink) {
      btnUnlink.classList.toggle("d-none", !linked);
    }
  }

  function renderLinkedMaintainers(usersRaw) {
    const listEl = q("link-maintainers-list");
    const countEl = q("link-maintainers-count");
    if (!listEl || !countEl) return;

    const users = Array.isArray(usersRaw)
      ? usersRaw.map((row) => (row && typeof row === "object" ? row : null)).filter(Boolean)
      : [];

    countEl.textContent = String(users.length);
    clearNode(listEl);

    if (!users.length) {
      const empty = document.createElement("div");
      empty.className = "text-secondary small";
      empty.textContent = "Keine Maintainer zugeordnet.";
      listEl.appendChild(empty);
      return;
    }

    users.forEach((user) => {
      const wrap = document.createElement("div");
      wrap.className = "link-maintainer-row";

      const avatar = document.createElement("div");
      avatar.className = "link-maintainer-avatar";
      const displayName = String(user.displayName || user.display_name || user.username || user.email || `#${user.id || "?"}`).trim();
      let avatarUrl = String(user.avatarUrl || user.avatar_url || "").trim();
      if (avatarUrl && avatarUrl.startsWith("/")) {
        const base = String(((statusDashboardState.status || {}).config || {}).admin_base_url || "").trim();
        if (base) {
          avatarUrl = `${base.replace(/\/+$/, "")}${avatarUrl}`;
        }
      }
      if (avatarUrl) {
        const img = document.createElement("img");
        img.src = avatarUrl;
        img.alt = displayName;
        img.loading = "lazy";
        img.referrerPolicy = "no-referrer";
        avatar.appendChild(img);
      } else {
        avatar.textContent = (displayName[0] || "?").toUpperCase();
      }

      const meta = document.createElement("div");
      meta.className = "link-maintainer-meta";
      const name = document.createElement("div");
      name.className = "link-maintainer-name";
      name.textContent = displayName;
      const detail = document.createElement("div");
      detail.className = "link-maintainer-sub";
      const username = String(user.username || "").trim();
      const email = String(user.email || "").trim();
      detail.textContent = [username, email].filter(Boolean).join(" · ") || `id: ${user.id || "-"}`;
      meta.appendChild(name);
      meta.appendChild(detail);

      wrap.appendChild(avatar);
      wrap.appendChild(meta);
      listEl.appendChild(wrap);
    });
  }

  function ensureLinkedMaintainersHydrated(usersRaw, linked) {
    if (maintainerState.hydrating) return;
    const adminBase = String(((statusDashboardState.status || {}).config || {}).admin_base_url || "").trim();
    if (!adminBase) return;
    const users = Array.isArray(usersRaw) ? usersRaw : [];
    const isEmpty = users.length === 0;
    const needsHydration = users.some((row) => {
      if (!row || typeof row !== "object") return false;
      return !String(row.username || row.email || row.displayName || row.display_name || "").trim();
    });
    if (!isEmpty && !needsHydration) return;
    if (isEmpty && maintainerState.hydratedOnce) return;

    maintainerState.hydrating = true;
    fetchJson("/api/panel/link-status", { cache: "no-store" })
      .then((payload) => {
        const rows = Array.isArray(payload.panel_linked_users) ? payload.panel_linked_users : [];
        if (statusDashboardState.status && statusDashboardState.status.config) {
          statusDashboardState.status.config.panel_linked_users = rows;
        }
        renderLinkedMaintainers(rows);
        maintainerState.hydratedOnce = true;
      })
      .catch(() => {
        // Keep UI stable when admin panel is temporarily unavailable.
      })
      .finally(() => {
        maintainerState.hydrating = false;
      });
  }

  function renderPanelSyncStatus(result, hadError = false) {
    const badge = q("panel-sync-badge");
    const missingEl = q("panel-sync-missing");
    const storageEl = q("panel-sync-storage-count");
    const softwareEl = q("panel-sync-software-count");
    const lastCheckEl = q("panel-sync-last-check");

    if (lastCheckEl) {
      lastCheckEl.textContent = panelSyncState.lastCheckAt || "-";
    }

    if (hadError || !result) {
      if (badge) {
        badge.classList.remove("text-bg-success", "text-bg-warning");
        badge.classList.add("text-bg-danger");
        badge.textContent = "fehler";
      }
      if (missingEl) missingEl.textContent = "Sync-Check fehlgeschlagen";
      if (storageEl) storageEl.textContent = "-";
      if (softwareEl) softwareEl.textContent = "-";
      return;
    }

    const missing = Array.isArray(result.missing) ? result.missing : [];
    const checksOk = !!result.ok;
    const stats = result.stats || {};
    if (badge) {
      badge.classList.remove("text-bg-danger", "text-bg-warning", "text-bg-success", "text-bg-secondary");
      badge.classList.add(checksOk ? "text-bg-success" : "text-bg-warning");
      badge.textContent = checksOk ? "vollständig" : "unvollständig";
    }
    if (missingEl) {
      missingEl.textContent = missing.length > 0 ? missing.join(", ") : "Keine";
    }
    if (storageEl) storageEl.textContent = String(stats.storageDevices ?? "-");
    if (softwareEl) softwareEl.textContent = String(stats.softwareRows ?? "-");
  }

  function renderNetwork(payload) {
    const data = payload.data || payload;
    networkState = data;
    statusDashboardState.network = data;

    const lan = (data.interfaces || {}).lan || {};
    const wifi = (data.interfaces || {}).wifi || {};
    const bt = (data.interfaces || {}).bluetooth || {};
    const routes = data.routes || {};
    const tailscale = data.tailscale || {};
    applyConnectivitySetupMode(data.connectivity_setup_mode || {});

    q("net-hostname").textContent = data.hostname || "-";
    q("net-gateway").textContent = routes.gateway || "-";
    q("net-gateway-mac").textContent = routes.gateway_mac || "-";
    q("net-dns").textContent = (routes.dns || []).join(", ") || "-";
    q("net-tailscale").textContent = tailscale.present ? (tailscale.ip ? `connected (${tailscale.ip})` : "installed (no IP)") : "not present";
    q("radio-tailscale").textContent = tailscale.present ? (tailscale.ip ? `connected (${tailscale.ip})` : "installed (no IP)") : "not present";

    q("lan-ifname").textContent = lan.ifname || "eth0";
    q("lan-enabled").textContent = yn(!!lan.enabled);
    q("lan-carrier").textContent = yn(!!lan.carrier);
    q("lan-connection").textContent = lan.connection || "-";
    q("lan-ip").textContent = lan.ip || "-";
    q("lan-mac").textContent = lan.mac || "-";

    q("wifi-ifname").textContent = wifi.ifname || "wlan0";
    q("wifi-enabled").textContent = yn(!!wifi.enabled);
    q("wifi-connected").textContent = yn(!!wifi.connected);
    q("wifi-ssid").textContent = wifi.ssid || "-";
    q("wifi-connection").textContent = wifi.connection || "-";
    q("wifi-bssid").textContent = wifi.bssid || "-";
    q("wifi-signal").textContent = Number.isInteger(wifi.signal) ? String(wifi.signal) : "-";
    q("wifi-frequency").textContent = Number.isInteger(wifi.frequency_mhz) ? `${wifi.frequency_mhz} MHz` : "-";
    q("wifi-security").textContent = wifi.security || "-";
    q("wifi-wpa-state").textContent = wifi.wpa_state || "-";
    q("wifi-ip").textContent = wifi.ip || "-";
    q("wifi-mac").textContent = wifi.mac || "-";

    q("bt-enabled").textContent = yn(!!bt.enabled);
    q("bt-discoverable").textContent = bt.discoverable === null || bt.discoverable === undefined ? "-" : yn(!!bt.discoverable);
    q("bt-pairable").textContent = bt.pairable === null || bt.pairable === undefined ? "-" : yn(!!bt.pairable);
    q("bt-discoverable-timeout").textContent = formatSeconds(bt.discoverable_timeout);
    q("bt-pairable-timeout").textContent = formatSeconds(bt.pairable_timeout);

    const btBadge = q("bt-enabled-badge");
    if (btBadge) {
      btBadge.classList.remove("text-bg-success", "text-bg-secondary");
      btBadge.classList.add(bt.enabled ? "text-bg-success" : "text-bg-secondary");
      btBadge.textContent = bt.enabled ? "aktiv" : "inaktiv";
    }

    els.btnWifiToggle.textContent = wifi.enabled ? "Disable Wi-Fi" : "Enable Wi-Fi";
    els.btnBtToggle.textContent = bt.enabled ? "Bluetooth ausschalten" : "Bluetooth einschalten";
    els.btnLanToggle.textContent = lan.enabled ? "Disable LAN" : "Enable LAN";
    if (data.security && typeof data.security === "object") {
      const profile = data.security.profile || {};
      const assessment = data.security.assessment || null;
      portalSecurityState.networkSecurity.enabled = !!profile.enabled;
      portalSecurityState.networkSecurity.trusted_wifi = Array.isArray(profile.trusted_wifi) ? profile.trusted_wifi : [];
      portalSecurityState.networkSecurity.trusted_lan = Array.isArray(profile.trusted_lan) ? profile.trusted_lan : [];
      portalSecurityState.networkSecurity.trusted_bluetooth = Array.isArray(profile.trusted_bluetooth) ? profile.trusted_bluetooth : [];
      portalSecurityState.networkSecurity.assessment = assessment;
      portalSecurityState.networkSecurity.catalog = (data.security && data.security.catalog) ? data.security.catalog : null;
      const perimeterToggle = q("security-perimeter-enabled");
      if (perimeterToggle) {
        perimeterToggle.checked = portalSecurityState.networkSecurity.enabled;
      }
      renderNetworkSecurityUi();
    }
    renderStatusHealthCard();
    renderStatusSoftwareSection();
  }

  async function refreshPanelFlagsLive() {
    const status = statusDashboardState.status || {};
    const cfg = status.config || {};
    const state = status.state || {};
    const panel = state.panel || cfg.panel_link_state || {};
    if (!panel.linked) return;
    const adminBaseUrl = String(cfg.admin_base_url || els.adminBase?.value || "").trim();
    if (!adminBaseUrl) return;
    try {
      const payload = await fetchJson("/api/panel/ping", {
        method: "POST",
        body: { admin_base_url: adminBaseUrl },
        timeoutMs: 9000,
      });
      if (payload.panel_device_flags && statusDashboardState.status && statusDashboardState.status.config) {
        statusDashboardState.status.config.panel_device_flags = payload.panel_device_flags;
        renderHeroPanelFlags(statusDashboardState.status.config);
      }
    } catch (_) {
      // Keep page usable if panel is temporarily unreachable.
    }
  }

  function renderSyncStatus(payload) {
    const data = payload && typeof payload === "object" ? payload : {};
    const put = (id, value) => {
      const el = q(id);
      if (el) el.textContent = value;
    };
    const enabled = !!data.enabled;
    put("sync-status-enabled", enabled ? "ja" : "nein");
    put("sync-status-last-at", data.last_sync_at || "-");
    put("sync-status-last-status", data.last_sync_status || "-");
    put("sync-status-last-direction", data.last_sync_direction || "-");
    put("sync-status-last-trigger", data.last_sync_triggered_by || "-");
    put("sync-status-last-message", data.last_sync_message || data.last_error || "-");

    const rules = Array.isArray(data.rules) ? data.rules : [];
    const groups = Array.from(
      new Set(
        rules
          .filter((rule) => !!(rule && rule.enabled))
          .map((rule) => String(rule.groupName || rule.group_name || "").trim())
          .filter(Boolean),
      ),
    );
    put("sync-status-groups", groups.length ? groups.join(", ") : "-");
  }

  async function refreshSyncStatus() {
    const payload = await fetchJson("/api/sync/status", { cache: "no-store" });
    renderSyncStatus(payload.data || {});
    return payload;
  }

  async function pullSyncConfig() {
    const payload = await fetchJson("/api/sync/pull-config", {
      method: "POST",
      body: {},
      timeoutMs: 15000,
    });
    toast(payload.message || "Sync-Konfiguration aktualisiert.", "success");
    await refreshSyncStatus();
  }

  async function runPortalSyncNow() {
    const payload = await fetchJson("/api/sync/run", {
      method: "POST",
      body: {
        direction: "bidirectional",
        pullConfig: true,
        triggeredBy: "portal_ui_manual",
      },
      timeoutMs: 30000,
    });
    toast(payload.message || "Synchronisierung erfolgreich.", "success");
    await refreshSyncStatus();
  }

  async function refreshStatus() {
    const data = await fetchJson("/api/status", { cache: "no-store" });
    renderStatus(data);
    return data;
  }

  async function refreshState() {
    const data = await fetchJson("/api/status/state", { cache: "no-store" });
    const base = await fetchJson("/api/status", { cache: "no-store" });
    try {
      const linkStatus = await fetchJson("/api/panel/link-status", { cache: "no-store" });
      if (linkStatus && typeof linkStatus === "object") {
        base.config = base.config || {};
        if (linkStatus.panel_api_keys && typeof linkStatus.panel_api_keys === "object") {
          base.config.panel_api_keys = linkStatus.panel_api_keys;
        }
        if (linkStatus.panel_api_key_bootstrap && typeof linkStatus.panel_api_key_bootstrap === "object") {
          base.config.panel_api_key_bootstrap = linkStatus.panel_api_key_bootstrap;
        }
        if (Array.isArray(linkStatus.panel_linked_users)) {
          base.config.panel_linked_users = linkStatus.panel_linked_users;
        }
      }
    } catch (_) {
      // Keep UI functional if panel link-status endpoint is temporarily unavailable.
    }
    base.state = data.state || base.state;
    renderStatus(base);
    return base;
  }

  async function refreshNetwork() {
    const data = await fetchJson("/api/network/info");
    renderNetwork(data);
    return data;
  }

  async function storageRegister(deviceId) {
    await fetchJson("/api/network/storage/register", { method: "POST", body: { device_id: deviceId, auto_mount: true } });
    toast("Speicher wurde registriert.", "success");
    await refreshStorageStatus();
  }

  async function storageIgnore(deviceId) {
    await fetchJson("/api/network/storage/ignore", { method: "POST", body: { device_id: deviceId } });
    toast("Gerät wird ignoriert.", "secondary");
    await refreshStorageStatus();
  }

  async function storageUnignore(deviceId) {
    await fetchJson("/api/network/storage/unignore", { method: "POST", body: { device_id: deviceId } });
    toast("Gerät wieder freigegeben.", "success");
    await refreshStorageStatus();
  }

  async function storageMount(deviceId) {
    await fetchJson("/api/network/storage/mount", { method: "POST", body: { device_id: deviceId }, timeoutMs: 25000 });
    toast("Speicher wurde gemountet.", "success");
    await refreshStorageStatus();
  }

  async function storageUnmount(deviceId) {
    await fetchJson("/api/network/storage/unmount", { method: "POST", body: { device_id: deviceId }, timeoutMs: 25000 });
    toast("Speicher wurde ausgehängt.", "success");
    await refreshStorageStatus();
  }

  async function storageToggleEnabled(deviceId, enabled) {
    await fetchJson("/api/network/storage/toggle-enabled", { method: "POST", body: { device_id: deviceId, enabled } });
    toast(enabled ? "Speicher aktiviert." : "Speicher deaktiviert.", "success");
    await refreshStorageStatus();
  }

  async function storageToggleAutoMount(deviceId, autoMount) {
    await fetchJson("/api/network/storage/toggle-automount", { method: "POST", body: { device_id: deviceId, auto_mount: autoMount } });
    toast(autoMount ? "Auto-Mount aktiviert." : "Auto-Mount deaktiviert.", "success");
    await refreshStorageStatus();
  }

  async function storageRemove(deviceId) {
    if (!window.confirm("Speicher-Konfiguration wirklich entfernen?")) return;
    await fetchJson("/api/network/storage/remove", { method: "POST", body: { device_id: deviceId } });
    toast("Speicher-Konfiguration entfernt.", "success");
    await refreshStorageStatus();
  }

  function storageTypeBadge(driveType = "", isInternal = false) {
    if (isInternal) return "internal";
    if (String(driveType || "").toLowerCase() === "usb") return "USB-Drive";
    return String(driveType || "extern");
  }

  function storageStatusBadge(item) {
    if (item && item.mounted) return { cls: "text-bg-success", text: "gemountet" };
    if (item && item.present) return { cls: "text-bg-warning", text: "vorhanden" };
    return { cls: "text-bg-secondary", text: "nicht vorhanden" };
  }

  async function storageFormat(deviceId, currentLabel = "") {
    const filesystemInput = (window.prompt("Dateisystem für Formatierung (ext4, vfat, exfat):", "vfat") || "").trim().toLowerCase();
    if (!filesystemInput) return;
    if (!["ext4", "vfat", "exfat"].includes(filesystemInput)) {
      toast("Ungültiges Dateisystem. Erlaubt: ext4, vfat, exfat", "danger");
      return;
    }
    const label = (window.prompt("Neues Label (optional):", currentLabel || "") || "").trim();
    const ok = window.confirm(`Gerät wirklich formatieren?\nDateisystem: ${filesystemInput}\nLabel: ${label || "-"}`);
    if (!ok) return;
    await fetchJson("/api/network/storage/format", {
      method: "POST",
      body: { device_id: deviceId, filesystem: filesystemInput, label },
      timeoutMs: 120000,
    });
    toast("Speicher wurde formatiert.", "success");
    await refreshStorageStatus();
  }

  function escapeHtml(input) {
    return String(input || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function setStorageFileManagerActive(active) {
    const host = q("storage-workspace");
    if (!host) return;
    host.classList.toggle("storage-file-manager-active", !!active);
    storageFmState.active = !!active;
  }

  function clearStoragePreviewObjectUrl() {
    if (storageFmPreviewObjectUrl) {
      try {
        window.URL.revokeObjectURL(storageFmPreviewObjectUrl);
      } catch (_) {
        // ignore URL cleanup failures
      }
      storageFmPreviewObjectUrl = "";
    }
  }

  async function storageFmFetchTree(deviceId, path = "") {
    const query = new URLSearchParams({ device_id: String(deviceId || ""), path: String(path || "") });
    return fetchJson(`/api/network/storage/file-manager/tree?${query.toString()}`);
  }

  async function storageFmFetchList(deviceId, path = "") {
    const query = new URLSearchParams({ device_id: String(deviceId || ""), path: String(path || "") });
    return fetchJson(`/api/network/storage/file-manager/list?${query.toString()}`);
  }

  async function storageFmFetchPreview(deviceId, path) {
    const query = new URLSearchParams({ device_id: String(deviceId || ""), path: String(path || "") });
    return fetchJson(`/api/network/storage/file-manager/preview?${query.toString()}`);
  }

  function storageFmFileUrl(deviceId, path, download = false) {
    const query = new URLSearchParams({
      device_id: String(deviceId || ""),
      path: String(path || ""),
    });
    if (download) query.set("download", "1");
    return `/api/network/storage/file-manager/file?${query.toString()}`;
  }

  function renderStorageFmBreadcrumb(treeData) {
    const host = q("storage-fm-breadcrumb");
    clearNode(host);
    const breadcrumb = Array.isArray(treeData.breadcrumb) ? treeData.breadcrumb : [];
    if (!breadcrumb.length) {
      host.textContent = "/";
      return;
    }
    breadcrumb.forEach((item, index) => {
      const part = document.createElement("span");
      part.className = "storage-fm-breadcrumb-item";
      part.textContent = item.name || "/";
      part.addEventListener("click", () => run(() => storageFileManagerLoadPath(item.path || "")));
      host.append(part);
      if (index < breadcrumb.length - 1) {
        host.append(document.createTextNode(" / "));
      }
    });
  }

  function renderStorageFmTree(treeData) {
    renderStorageFmBreadcrumb(treeData);
    const host = q("storage-fm-tree-list");
    clearNode(host);
    const directories = Array.isArray(treeData.directories) ? treeData.directories : [];
    if (!directories.length) {
      const empty = document.createElement("div");
      empty.className = "text-secondary";
      empty.textContent = "Keine Unterordner.";
      host.append(empty);
      return;
    }
    for (const dir of directories) {
      const row = document.createElement("div");
      row.className = "list-group-item px-0 d-flex align-items-center justify-content-between gap-2";
      const label = document.createElement("span");
      label.className = "text-truncate";
      if (dir.blocked || dir.is_symlink) {
        label.innerHTML = `<i class="bi bi-link-45deg me-1 text-danger"></i>${escapeHtml(dir.name || "Symlink")} <span class="text-danger small">(blockiert)</span>`;
      } else {
        label.innerHTML = `<i class="bi bi-folder2 me-1"></i>${escapeHtml(dir.name || "Ordner")}`;
        const openBtn = document.createElement("button");
        openBtn.type = "button";
        openBtn.className = "btn btn-sm btn-outline-secondary d-inline-flex align-items-center justify-content-center";
        openBtn.title = "Verzeichnis öffnen";
        openBtn.setAttribute("aria-label", "Verzeichnis öffnen");
        // Lucide-style folder-open icon as requested.
        openBtn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 14a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 2h5a2 2 0 0 1 2 2v2"/><path d="M3 20h13"/><path d="M18 6h1a2 2 0 0 1 2 2v3"/><path d="M14 18h7"/><path d="m18 14 4 4-4 4"/></svg>';
        openBtn.addEventListener("click", () => run(() => storageFileManagerLoadPath(dir.path || "")));
        row.append(label, openBtn);
        host.append(row);
        continue;
      }
      row.append(label);
      host.append(row);
    }
  }

  function storageFmEntryIcon(entry) {
    if (entry.type === "symlink") return "bi-link-45deg";
    if (entry.type === "directory") return "bi-folder2";
    if (entry.type === "file") return "bi-file-earmark";
    return "bi-file-earmark-binary";
  }

  function renderStorageFmEntries(entries) {
    const host = q("storage-fm-list");
    clearNode(host);
    storageFmState.entries = Array.isArray(entries) ? entries : [];
    if (!storageFmState.entries.length) {
      const empty = document.createElement("div");
      empty.className = "text-secondary";
      empty.textContent = "Dieses Verzeichnis ist leer.";
      host.append(empty);
      return;
    }
    for (const entry of storageFmState.entries) {
      const path = String(entry.path || "");
      const row = document.createElement("div");
      row.className = "list-group-item px-0 storage-fm-entry";
      if (entry.blocked || entry.is_symlink) {
        row.classList.add("storage-fm-entry-blocked");
      }
      if (storageFmState.activeEntryPath === path) {
        row.classList.add("active");
      }

      const top = document.createElement("div");
      top.className = "d-flex justify-content-between align-items-center gap-2";
      const left = document.createElement("div");
      left.className = "d-flex align-items-center gap-2 flex-grow-1 text-truncate";
      const check = document.createElement("input");
      check.type = "checkbox";
      check.className = "form-check-input mt-0";
      check.checked = storageFmState.selectedPaths.has(path);
      if (entry.blocked || entry.is_symlink) {
        check.disabled = true;
      }
      check.addEventListener("click", (event) => event.stopPropagation());
      check.addEventListener("change", () => {
        if (check.checked) storageFmState.selectedPaths.add(path);
        else storageFmState.selectedPaths.delete(path);
      });
      const icon = document.createElement("i");
      icon.className = `bi ${storageFmEntryIcon(entry)}`;
      const title = document.createElement("span");
      title.className = "text-truncate";
      title.textContent = entry.name || "-";
      left.append(check, icon, title);

      const right = document.createElement("div");
      right.className = "small text-secondary";
      const sizeText = entry.type === "directory" ? "Ordner" : formatBytes(entry.size_bytes || 0);
      right.textContent = sizeText;
      top.append(left, right);

      const meta = document.createElement("div");
      meta.className = "small text-secondary";
      const blockedText = (entry.blocked || entry.is_symlink) ? " | blockiert" : "";
      meta.textContent = `${entry.type || "entry"} | geändert: ${entry.modified_at || "-"}${blockedText}`;

      if (!(entry.blocked || entry.is_symlink)) {
        row.addEventListener("click", () => run(() => storageFileManagerSelectEntry(path)));
        row.addEventListener("dblclick", () => {
          if (entry.type === "directory") {
            run(() => storageFileManagerLoadPath(path));
          }
        });
      } else {
        row.addEventListener("click", () => {
          toast("Symlink ist aus Sicherheitsgründen blockiert.", "danger");
        });
      }

      if (entry.type === "directory" && !(entry.blocked || entry.is_symlink)) {
        const actions = document.createElement("div");
        actions.className = "d-flex gap-1 mt-1";
        const openBtn = document.createElement("button");
        openBtn.type = "button";
        openBtn.className = "btn btn-outline-secondary btn-sm mt-1";
        openBtn.textContent = "Öffnen";
        openBtn.addEventListener("click", (event) => {
          event.stopPropagation();
          run(() => storageFileManagerLoadPath(path));
        });
        const renameBtn = document.createElement("button");
        renameBtn.type = "button";
        renameBtn.className = "btn btn-outline-secondary btn-sm mt-1";
        renameBtn.textContent = "✏ Rename";
        renameBtn.addEventListener("click", (event) => {
          event.stopPropagation();
          run(() => storageFileManagerOpenRenameModal(path));
        });
        actions.append(openBtn, renameBtn);
        row.append(top, meta, actions);
      } else if (entry.type === "file" && !(entry.blocked || entry.is_symlink)) {
        const actions = document.createElement("div");
        actions.className = "d-flex gap-1 mt-1";
        const dl = document.createElement("a");
        dl.className = "btn btn-outline-secondary btn-sm mt-1";
        dl.href = storageFmFileUrl(storageFmState.deviceId, path, true);
        dl.innerHTML = "⬇ Download";
        dl.addEventListener("click", (event) => event.stopPropagation());
        const renameBtn = document.createElement("button");
        renameBtn.type = "button";
        renameBtn.className = "btn btn-outline-secondary btn-sm mt-1";
        renameBtn.textContent = "✏ Rename";
        renameBtn.addEventListener("click", (event) => {
          event.stopPropagation();
          run(() => storageFileManagerOpenRenameModal(path));
        });
        actions.append(dl, renameBtn);
        row.append(top, meta, actions);
      } else {
        row.append(top, meta);
      }
      host.append(row);
    }
  }

  function renderStorageFmPreview(preview) {
    const host = q("storage-fm-preview");
    clearStoragePreviewObjectUrl();
    clearNode(host);

    const info = preview || {};
    const appendDownload = () => {
      if (String(info.type || "") !== "file" || !info.path) return;
      const wrap = document.createElement("div");
      wrap.className = "mb-2";
      const dl = document.createElement("a");
      dl.className = "btn btn-outline-secondary btn-sm";
      dl.href = storageFmFileUrl(storageFmState.deviceId, info.path, true);
      dl.innerHTML = "⬇ Download";
      wrap.append(dl);
      host.append(wrap);
    };
    const appendMeta = () => {
      const meta = document.createElement("dl");
      meta.className = "portal-kv mb-0 mt-2";
      const rows = [
        ["Name", info.name || "-"],
        ["Typ", info.type || "-"],
        ["MIME", info.mime_type || "-"],
        ["Größe", info.type === "directory" ? "-" : formatBytes(info.size_bytes || 0)],
        ["Pfad", info.path || "/"],
        ["Geändert", info.modified_at || "-"],
      ];
      for (const [label, value] of rows) {
        const dt = document.createElement("dt");
        dt.textContent = label;
        const dd = document.createElement("dd");
        dd.textContent = String(value || "-");
        meta.append(dt, dd);
      }
      host.append(meta);
    };

    const kind = String(info.preview_kind || "info");
    if (kind === "blocked") {
      const blocked = document.createElement("div");
      blocked.className = "alert alert-danger py-2 mb-0";
      blocked.textContent = info.preview_message || "Dieser Eintrag ist blockiert.";
      host.append(blocked);
      appendDownload();
      appendMeta();
      return;
    }
    if (kind === "too_large") {
      const warning = document.createElement("div");
      warning.className = "alert alert-warning py-2 mb-0";
      warning.textContent = info.preview_message || "Datei zu groß für Vorschau.";
      host.append(warning);
      appendDownload();
      appendMeta();
      return;
    }
    if (kind === "text") {
      const pre = document.createElement("pre");
      pre.className = "code-block mb-0";
      pre.style.maxHeight = "360px";
      pre.style.overflow = "auto";
      pre.textContent = String(info.text_excerpt || "");
      host.append(pre);
      appendDownload();
      appendMeta();
      return;
    }
    if (kind === "image" && info.file_url) {
      const img = document.createElement("img");
      img.className = "storage-fm-preview-image";
      img.alt = info.name || "Preview";
      img.src = `${info.file_url}&t=${Date.now()}`;
      host.append(img);
      appendDownload();
      appendMeta();
      return;
    }
    if (kind === "pdf" && info.file_url) {
      const link = document.createElement("a");
      link.href = `${info.file_url}&t=${Date.now()}`;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.className = "btn btn-outline-secondary btn-sm";
      link.textContent = "PDF öffnen";
      host.append(link);
      appendDownload();
      appendMeta();
      return;
    }
    if (Array.isArray(info.children_preview) && info.children_preview.length) {
      const list = document.createElement("ul");
      list.className = "small mb-0";
      for (const item of info.children_preview) {
        const li = document.createElement("li");
        li.textContent = item;
        list.append(li);
      }
      host.append(list);
      appendDownload();
      appendMeta();
      return;
    }

    const fallback = document.createElement("div");
    fallback.className = "text-secondary";
    fallback.textContent = info.preview_message || "Keine direkte Vorschau verfügbar.";
    host.append(fallback);
    appendDownload();
    appendMeta();
  }

  async function storageFileManagerSelectEntry(path) {
    if (!storageFmState.active || !storageFmState.deviceId) return;
    storageFmState.activeEntryPath = String(path || "");
    renderStorageFmEntries(storageFmState.entries);
    const payload = await storageFmFetchPreview(storageFmState.deviceId, storageFmState.activeEntryPath);
    renderStorageFmPreview(payload.data || {});
  }

  async function storageFileManagerLoadPath(path) {
    if (!storageFmState.active || !storageFmState.deviceId) return;
    storageFmState.currentPath = String(path || "");
    storageFmState.activeEntryPath = "";
    storageFmState.selectedPaths = new Set();
    q("storage-fm-preview").textContent = "Datei oder Ordner auswählen.";

    const [treePayload, listPayload] = await Promise.all([
      storageFmFetchTree(storageFmState.deviceId, storageFmState.currentPath),
      storageFmFetchList(storageFmState.deviceId, storageFmState.currentPath),
    ]);
    const treeData = treePayload.data || {};
    const listData = listPayload.data || {};
    storageFmState.currentPath = String(listData.current_path || treeData.current_path || "");
    q("storage-fm-path-badge").textContent = `/${storageFmState.currentPath}`.replace(/\/$/, "") || "/";
    const upBtn = q("btn-storage-fm-dir-up");
    if (upBtn) {
      upBtn.disabled = !storageFmState.currentPath;
    }
    renderStorageFmTree(treeData);
    renderStorageFmEntries(listData.entries || []);
  }

  async function storageFileManagerGoUp() {
    if (!storageFmState.active || !storageFmState.deviceId) return;
    const current = String(storageFmState.currentPath || "").trim();
    if (!current) return;
    const parts = current.split("/").filter(Boolean);
    parts.pop();
    await storageFileManagerLoadPath(parts.join("/"));
  }

  async function openStorageFileManager(deviceId) {
    const deviceKey = String(deviceId || "");
    const item = knownDrivesById.get(deviceKey) || knownStorageById.get(deviceKey);
    if (!item) {
      toast("Laufwerkdaten nicht gefunden.", "danger");
      return;
    }
    if (!item.mounted || !item.present) {
      toast("Laufwerk ist nicht gemountet.", "danger");
      return;
    }
    storageFmState.deviceId = String(item.id || "");
    storageFmState.deviceName = item.drive_name || item.name || item.label || item.uuid || item.id || "Storage";
    storageFmState.currentPath = "";
    storageFmState.entries = [];
    storageFmState.selectedPaths = new Set();
    storageFmState.activeEntryPath = "";
    storageFmState.uploadQueue = [];
    storageFmState.uploadRunning = false;
    q("storage-fm-device-badge").textContent = storageFmState.deviceName;
    q("storage-fm-path-badge").textContent = "/";
    q("btn-storage-fm-dir-up").disabled = true;
    q("storage-fm-preview").textContent = "Lade Verzeichnis...";
    q("storage-fm-upload-progress-wrap").classList.add("d-none");
    renderStorageFmUploadQueue();
    setStorageFileManagerActive(true);
    await storageFileManagerLoadPath("");
  }

  function closeStorageFileManager() {
    storageFmState.deviceId = "";
    storageFmState.deviceName = "";
    storageFmState.currentPath = "";
    storageFmState.entries = [];
    storageFmState.selectedPaths = new Set();
    storageFmState.activeEntryPath = "";
    storageFmState.uploadQueue = [];
    storageFmState.uploadRunning = false;
    clearStoragePreviewObjectUrl();
    setStorageFileManagerActive(false);
    q("storage-fm-preview").textContent = "Datei oder Ordner auswählen.";
    q("btn-storage-fm-dir-up").disabled = true;
    renderStorageFmUploadQueue();
  }

  function storageFileManagerSelectAll() {
    const selected = new Set();
    for (const entry of storageFmState.entries) {
      if (entry && entry.path && !(entry.blocked || entry.is_symlink)) selected.add(String(entry.path));
    }
    storageFmState.selectedPaths = selected;
    renderStorageFmEntries(storageFmState.entries);
  }

  function storageFileManagerUnselectAll() {
    storageFmState.selectedPaths = new Set();
    renderStorageFmEntries(storageFmState.entries);
  }

  async function storageFileManagerDeleteSelected() {
    const paths = Array.from(storageFmState.selectedPaths);
    if (!paths.length) {
      toast("Keine Einträge ausgewählt.", "secondary");
      return;
    }
    storageDeletePendingPaths = paths;
    q("storage-delete-count").textContent = String(paths.length);
    q("storage-delete-confirm-word").value = "";
    const requireHard = !!portalSecurityState.storageDeleteHardcoreMode;
    q("storage-delete-confirm-wrap").classList.toggle("d-none", !requireHard);
    const modal = bootstrap.Modal.getOrCreateInstance(q("storageDeleteConfirmModal"));
    modal.show();
  }

  async function storageFileManagerDeleteConfirmed() {
    const paths = Array.isArray(storageDeletePendingPaths) ? storageDeletePendingPaths : [];
    if (!paths.length) {
      toast("Keine Einträge ausgewählt.", "secondary");
      return;
    }
    const requireHardConfirm = !!portalSecurityState.storageDeleteHardcoreMode;
    const confirmWord = String(q("storage-delete-confirm-word").value || "").trim().toUpperCase();
    if (requireHardConfirm && confirmWord !== "DELETE") {
      toast("Löschen nicht bestätigt. Bitte DELETE eingeben.", "danger");
      return;
    }
    const payload = await fetchJson("/api/network/storage/file-manager/delete", {
      method: "POST",
      body: {
        device_id: storageFmState.deviceId,
        paths,
        confirm_word: confirmWord,
        confirm_count: paths.length,
        require_hard_confirm: requireHardConfirm,
      },
      timeoutMs: 45000,
    });
    const modal = bootstrap.Modal.getOrCreateInstance(q("storageDeleteConfirmModal"));
    modal.hide();
    storageDeletePendingPaths = [];
    const data = payload.data || {};
    toast(`${data.deleted_count || 0} Eintrag/Einträge gelöscht.`, "success");
    if ((data.failed_count || 0) > 0) {
      toast(`${data.failed_count} Eintrag/Einträge konnten nicht gelöscht werden.`, "danger");
    }
    await storageFileManagerLoadPath(storageFmState.currentPath);
  }

  function storageFmHumanSize(bytes) {
    return formatBytes(bytes || 0);
  }

  function renderStorageFmUploadQueue() {
    const queueEl = q("storage-fm-upload-queue");
    clearNode(queueEl);
    if (!storageFmState.uploadQueue.length) {
      const empty = document.createElement("li");
      empty.className = "list-group-item text-secondary";
      empty.setAttribute("data-empty", "1");
      empty.textContent = "Keine Dateien ausgewählt.";
      queueEl.append(empty);
      return;
    }
    storageFmState.uploadQueue.forEach((item, idx) => {
      const li = document.createElement("li");
      li.className = "list-group-item d-flex justify-content-between align-items-center gap-2";
      li.innerHTML = `
        <div class="text-truncate" style="max-width:72%;">
          <div class="text-truncate"><i class="bi bi-file-earmark me-1"></i>${escapeHtml(item.file.name || "Datei")}</div>
          <div class="small text-secondary">${storageFmHumanSize(item.file.size || 0)}</div>
        </div>
      `;
      const del = document.createElement("button");
      del.type = "button";
      del.className = "btn btn-sm btn-outline-danger";
      del.textContent = "✖";
      del.disabled = storageFmState.uploadRunning;
      del.addEventListener("click", () => {
        storageFmState.uploadQueue.splice(idx, 1);
        renderStorageFmUploadQueue();
      });
      li.append(del);
      queueEl.append(li);
    });
  }

  function storageFmAddFiles(fileList) {
    Array.from(fileList || []).forEach((file) => {
      if (!file) return;
      if ((file.size || 0) > STORAGE_FM_UPLOAD_MAX_FILE_BYTES) {
        toast(`${file.name}: Datei ist größer als 512 MB.`, "danger");
        return;
      }
      storageFmState.uploadQueue.push({ file });
    });
    renderStorageFmUploadQueue();
  }

  function storageFmResolveRenamePath() {
    const selected = Array.from(storageFmState.selectedPaths || []);
    if (selected.length === 1) return selected[0];
    if (selected.length > 1) {
      throw new Error("Für Rename bitte genau einen Eintrag auswählen.");
    }
    if (storageFmState.activeEntryPath) return String(storageFmState.activeEntryPath);
    throw new Error("Bitte zuerst einen Eintrag auswählen.");
  }

  async function storageFileManagerCreateFolder() {
    const modal = bootstrap.Modal.getOrCreateInstance(q("storageNewFolderModal"));
    q("storage-new-folder-name").value = "";
    modal.show();
  }

  async function storageFileManagerCreateFolderConfirmed() {
    if (!storageFmState.active || !storageFmState.deviceId) {
      throw new Error("Kein Laufwerk ausgewählt.");
    }
    const folderName = String(q("storage-new-folder-name").value || "").trim();
    if (!folderName) {
      throw new Error("Ordnername darf nicht leer sein.");
    }
    await fetchJson("/api/network/storage/file-manager/mkdir", {
      method: "POST",
      body: {
        device_id: storageFmState.deviceId,
        path: storageFmState.currentPath || "",
        name: folderName,
      },
      timeoutMs: 20000,
    });
    bootstrap.Modal.getOrCreateInstance(q("storageNewFolderModal")).hide();
    toast("Ordner erstellt.", "success");
    await storageFileManagerLoadPath(storageFmState.currentPath);
  }

  async function storageFileManagerOpenRenameModal(path) {
    const targetPath = String(path || "").trim();
    if (!targetPath) {
      throw new Error("Ungültiger Eintrag.");
    }
    const entry = (storageFmState.entries || []).find((item) => String(item.path || "") === targetPath);
    const currentName = String(entry?.name || targetPath.split("/").pop() || "").trim();
    storageRenamePendingPath = targetPath;
    q("storage-rename-current-name").textContent = currentName || "-";
    q("storage-rename-new-name").value = currentName || "";
    bootstrap.Modal.getOrCreateInstance(q("storageRenameModal")).show();
  }

  async function storageFileManagerRenameConfirmed() {
    const targetPath = String(storageRenamePendingPath || "").trim();
    if (!targetPath) {
      throw new Error("Kein Eintrag für Rename ausgewählt.");
    }
    const entry = (storageFmState.entries || []).find((item) => String(item.path || "") === targetPath);
    const currentName = String(entry?.name || targetPath.split("/").pop() || "").trim();
    const newName = String(q("storage-rename-new-name").value || "").trim();
    if (!newName) {
      throw new Error("Neuer Name darf nicht leer sein.");
    }
    if (newName === currentName) {
      bootstrap.Modal.getOrCreateInstance(q("storageRenameModal")).hide();
      return;
    }
    await fetchJson("/api/network/storage/file-manager/rename", {
      method: "POST",
      body: {
        device_id: storageFmState.deviceId,
        path: targetPath,
        new_name: newName,
      },
      timeoutMs: 20000,
    });
    bootstrap.Modal.getOrCreateInstance(q("storageRenameModal")).hide();
    storageRenamePendingPath = "";
    toast("Eintrag umbenannt.", "success");
    await storageFileManagerLoadPath(storageFmState.currentPath);
  }

  async function storageFileManagerRenameSelected() {
    const targetPath = storageFmResolveRenamePath();
    await storageFileManagerOpenRenameModal(targetPath);
  }

  function storageFmSetUploadProgress(current, total, text = "") {
    const wrap = q("storage-fm-upload-progress-wrap");
    const bar = q("storage-fm-upload-progress-bar");
    const label = q("storage-fm-upload-progress-text");
    wrap.classList.remove("d-none");
    const percent = total > 0 ? Math.floor((current / total) * 100) : 0;
    bar.style.width = `${percent}%`;
    bar.textContent = `${percent}%`;
    label.textContent = text || "-";
  }

  function xhrUploadStorageFile(url, formData, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", url, true);
      xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
      xhr.onload = () => {
        let payload = {};
        try {
          payload = xhr.responseText ? JSON.parse(xhr.responseText) : {};
        } catch (_) {
          payload = {};
        }
        if (xhr.status >= 200 && xhr.status < 300 && payload.ok !== false) {
          resolve(payload);
        } else {
          const err = payload?.error?.message || payload?.message || `Upload fehlgeschlagen (${xhr.status})`;
          reject(new Error(err));
        }
      };
      xhr.onerror = () => reject(new Error("Netzwerkfehler beim Upload"));
      if (xhr.upload && typeof onProgress === "function") {
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) onProgress(e.loaded, e.total);
        };
      }
      xhr.send(formData);
    });
  }

  async function storageFmUploadAll() {
    if (!storageFmState.active || !storageFmState.deviceId) {
      toast("Kein Laufwerk ausgewählt.", "danger");
      return;
    }
    if (!storageFmState.uploadQueue.length) {
      toast("Keine Dateien ausgewählt.", "secondary");
      return;
    }
    if (storageFmState.uploadRunning) return;

    storageFmState.uploadRunning = true;
    renderStorageFmUploadQueue();
    const queue = [...storageFmState.uploadQueue];
    const errors = [];
    let uploadedCount = 0;

    for (let idx = 0; idx < queue.length; idx += 1) {
      const item = queue[idx];
      const fd = new FormData();
      fd.append("device_id", storageFmState.deviceId);
      fd.append("path", storageFmState.currentPath || "");
      fd.append("files", item.file, item.file.name);
      storageFmSetUploadProgress(0, item.file.size || 1, `Upload ${idx + 1}/${queue.length}: ${item.file.name}`);
      try {
        await xhrUploadStorageFile("/api/network/storage/file-manager/upload", fd, (loaded, total) => {
          storageFmSetUploadProgress(loaded, total, `Upload ${idx + 1}/${queue.length}: ${item.file.name}`);
        });
        uploadedCount += 1;
      } catch (err) {
        errors.push(`${item.file.name}: ${err.message || "Upload fehlgeschlagen"}`);
      }
    }

    storageFmState.uploadRunning = false;
    storageFmState.uploadQueue = [];
    renderStorageFmUploadQueue();
    storageFmSetUploadProgress(1, 1, "Upload abgeschlossen");
    await storageFileManagerLoadPath(storageFmState.currentPath);
    if (uploadedCount > 0) {
      toast(`${uploadedCount} Datei(en) hochgeladen.`, "success");
    }
    if (errors.length) {
      toast(errors.join(" | "), "danger");
    }
  }

  function renderStorageNew(list) {
    const host = q("storage-new-list");
    clearNode(host);
    if (!Array.isArray(list) || list.length === 0) {
      const empty = document.createElement("div");
      empty.className = "text-secondary";
      empty.textContent = "Keine neuen Geräte erkannt.";
      host.append(empty);
      return;
    }
    for (const item of list) {
      const row = document.createElement("div");
      row.className = "list-group-item px-0";
      const top = document.createElement("div");
      top.className = "d-flex justify-content-between align-items-center gap-2";
      const title = document.createElement("strong");
      title.textContent = item.label || item.uuid || item.part_uuid || item.device_path || "Unbekanntes Gerät";
      const badge = document.createElement("span");
      badge.className = "badge text-bg-warning";
      badge.textContent = "neu";
      top.append(title, badge);

      const meta = document.createElement("div");
      meta.className = "text-secondary mb-2";
      const fs = item.filesystem || "-";
      const size = formatBytes(item.size_bytes);
      const path = item.device_path || "-";
      const ident = item.uuid || item.part_uuid || "-";
      meta.textContent = `FS: ${fs} | Größe: ${size} | Pfad: ${path} | ID: ${ident}`;

      const actions = document.createElement("div");
      actions.className = "d-flex gap-2";
      const addBtn = document.createElement("button");
      addBtn.className = "btn btn-outline-success btn-sm";
      addBtn.textContent = "Als Speicher hinzufügen";
      addBtn.addEventListener("click", () => run(() => storageRegister(item.id)));
      const ignoreBtn = document.createElement("button");
      ignoreBtn.className = "btn btn-outline-secondary btn-sm";
      ignoreBtn.textContent = "Ignorieren";
      ignoreBtn.addEventListener("click", () => run(() => storageIgnore(item.id)));
      actions.append(addBtn, ignoreBtn);

      row.append(top, meta, actions);
      host.append(row);
    }
  }

  function renderStorageKnown(list) {
    const host = q("storage-known-list");
    clearNode(host);
    if (!Array.isArray(list) || list.length === 0) {
      const empty = document.createElement("div");
      empty.className = "text-secondary";
      empty.textContent = "Keine registrierten Speicher.";
      host.append(empty);
      return;
    }
    for (const item of list) {
      const row = document.createElement("div");
      row.className = "list-group-item px-0";

      const top = document.createElement("div");
      top.className = "d-flex justify-content-between align-items-center gap-2";
      const title = document.createElement("strong");
      title.textContent = item.name || item.label || item.uuid || item.part_uuid || "Speicher";
      const statusBadge = document.createElement("span");
      if (item.mounted) {
        statusBadge.className = "badge text-bg-success";
        statusBadge.textContent = "gemountet";
      } else if (item.present) {
        statusBadge.className = "badge text-bg-warning";
        statusBadge.textContent = "vorhanden";
      } else {
        statusBadge.className = "badge text-bg-secondary";
        statusBadge.textContent = "nicht vorhanden";
      }
      top.append(title, statusBadge);

      const meta = document.createElement("div");
      meta.className = "text-secondary mb-2";
      const fs = item.filesystem || "-";
      const size = formatBytes(item.size_bytes);
      const mountPath = item.mount_path || "-";
      const currentMount = item.current_mount_path || "-";
      const ident = item.uuid || item.part_uuid || "-";
      meta.textContent = `FS: ${fs} | Größe: ${size} | Mount: ${mountPath} | Aktuell: ${currentMount} | ID: ${ident}`;

      const extra = document.createElement("div");
      extra.className = "text-secondary mb-2";
      extra.textContent = `Enabled: ${item.is_enabled ? "yes" : "no"} | Auto-Mount: ${item.auto_mount ? "yes" : "no"} | Last seen: ${item.last_seen_at || "-"}${item.last_error ? ` | Fehler: ${item.last_error}` : ""}`;

      const actionsHint = document.createElement("div");
      actionsHint.className = "small text-secondary";
      actionsHint.textContent = "Aktionen über Icons in der Laufwerke-Karte.";

      row.append(top, meta, extra, actionsHint);
      host.append(row);
    }
  }

  function renderStorageIgnored(list) {
    const host = q("storage-ignored-list");
    clearNode(host);
    if (!Array.isArray(list) || list.length === 0) {
      const empty = document.createElement("div");
      empty.className = "text-secondary";
      empty.textContent = "Keine ignorierten Geräte.";
      host.append(empty);
      return;
    }
    for (const item of list) {
      const row = document.createElement("div");
      row.className = "list-group-item px-0";
      const top = document.createElement("div");
      top.className = "d-flex justify-content-between align-items-center gap-2";
      const title = document.createElement("strong");
      title.textContent = item.label || item.uuid || item.part_uuid || item.id || "Ignoriertes Gerät";
      const badge = document.createElement("span");
      badge.className = `badge ${item.present ? "text-bg-warning" : "text-bg-secondary"}`;
      badge.textContent = item.present ? "angeschlossen" : "nicht angeschlossen";
      top.append(title, badge);

      const meta = document.createElement("div");
      meta.className = "text-secondary mb-2";
      meta.textContent = `ID: ${item.id || "-"} | FS: ${item.filesystem || "-"} | Größe: ${formatBytes(item.size_bytes)} | Pfad: ${item.device_path || "-"}`;

      const actions = document.createElement("div");
      actions.className = "d-flex gap-2";
      const unignoreBtn = document.createElement("button");
      unignoreBtn.className = "btn btn-outline-primary btn-sm";
      unignoreBtn.textContent = "Zurückholen";
      unignoreBtn.addEventListener("click", () => run(() => storageUnignore(item.id)));
      actions.append(unignoreBtn);

      row.append(top, meta, actions);
      host.append(row);
    }
  }

  function processStorageDeltas(data) {
    const known = Array.isArray(data.known) ? data.known : [];
    const newer = Array.isArray(data.new) ? data.new : [];
    const nextStates = new Map();
    for (const item of known) {
      nextStates.set(item.id, `${item.present ? 1 : 0}:${item.mounted ? 1 : 0}`);
    }
    const nextNew = new Set(newer.map((item) => item.id));

    if (storageInitialized) {
      for (const item of newer) {
        if (!knownNewStorageIds.has(item.id)) {
          const label = item.label || item.uuid || item.part_uuid || item.device_path || "Unbekannt";
          toast(`Neues USB-Gerät erkannt: ${label}`, "success");
        }
      }
      for (const item of known) {
        const previous = knownStorageStates.get(item.id);
        const current = `${item.present ? 1 : 0}:${item.mounted ? 1 : 0}`;
        if (previous && previous !== current) {
          if (!item.present) {
            toast(`Speicher getrennt: ${item.name || item.label || item.id}`, "secondary");
          } else if (item.mounted) {
            toast(`Speicher gemountet: ${item.name || item.label || item.id}`, "success");
          } else {
            toast(`Speicher vorhanden, nicht gemountet: ${item.name || item.label || item.id}`, "secondary");
          }
        }
      }
    }
    knownStorageStates = nextStates;
    knownNewStorageIds = nextNew;
    storageInitialized = true;
  }

  function renderStorageInternal(internal) {
    const info = internal || {};
    const statusEl = q("storage-internal-status");
    const progress = q("storage-internal-progress");
    const loopTotal = Number(info.loop_total_bytes ?? info.total_bytes ?? 0);
    const loopUsed = Number(info.loop_used_bytes ?? info.used_bytes ?? 0);
    const loopFree = Number(info.loop_free_bytes ?? info.free_bytes ?? Math.max(0, loopTotal - loopUsed));
    const loopPercentRaw = info.loop_used_percent ?? (loopTotal > 0 ? (loopUsed / loopTotal) * 100 : 0);
    const percent = Math.max(0, Math.min(100, Number(loopPercentRaw || 0)));
    q("storage-internal-name").textContent = "Interner Medienspeicher (Loop)";
    q("storage-internal-image").textContent = info.image_path || "-";
    q("storage-internal-source").textContent = info.mounted_source || "-";
    q("storage-internal-mount").textContent = info.mount_path || "-";
    q("storage-internal-fs").textContent = info.filesystem || info.expected_filesystem || "-";
    q("storage-internal-size").textContent = formatBytes(loopTotal);
    q("storage-internal-used").textContent = formatBytes(loopUsed);
    q("storage-internal-free").textContent = formatBytes(loopFree);
    q("storage-internal-percent").textContent = `${percent}%`;
    progress.style.width = `${percent}%`;
    progress.textContent = `${percent}%`;
    progress.classList.remove("bg-success", "bg-warning", "bg-danger");
    if (percent >= 90) progress.classList.add("bg-danger");
    else if (percent >= 75) progress.classList.add("bg-warning");
    else progress.classList.add("bg-success");

    statusEl.className = "badge";
    if (info.mounted) {
      statusEl.classList.add("text-bg-success");
      statusEl.textContent = "gemountet";
    } else if (info.present) {
      statusEl.classList.add("text-bg-warning");
      statusEl.textContent = "vorhanden";
    } else {
      statusEl.classList.add("text-bg-secondary");
      statusEl.textContent = "missing";
    }
  }

  function renderStorageDrives(drives) {
    const host = q("storage-drives-list");
    clearNode(host);
    if (!Array.isArray(drives) || drives.length === 0) {
      const empty = document.createElement("div");
      empty.className = "col-12 text-secondary";
      empty.textContent = "Keine Laufwerke erkannt.";
      host.append(empty);
      return;
    }
    for (const d of drives) {
      const col = document.createElement("div");
      col.className = "col-12 col-md-6";
      const card = document.createElement("div");
      card.className = "border rounded p-2 h-100";
      const top = document.createElement("div");
      top.className = "d-flex justify-content-between align-items-center gap-2 mb-1";
      const title = document.createElement("strong");
      title.textContent = d.drive_name || d.id || "Laufwerk";
      title.className = "text-truncate";
      const typeBadge = document.createElement("span");
      typeBadge.className = "badge text-bg-light border text-dark";
      const typeIcon = document.createElement("i");
      const isInternal = !!d.is_internal;
      const isUsb = !isInternal && String(d.drive_type || "").toLowerCase() === "usb";
      typeIcon.className = isInternal ? "bi bi-hdd-stack me-1" : (isUsb ? "bi bi-usb-drive me-1" : "bi bi-device-hdd me-1");
      typeBadge.append(typeIcon, document.createTextNode(isInternal ? "internal" : (isUsb ? "USB-Drive" : (d.drive_type || "extern"))));
      const badge = document.createElement("span");
      badge.className = `badge ${d.mounted ? "text-bg-success" : (d.present ? "text-bg-warning" : "text-bg-secondary")}`;
      badge.textContent = d.mounted ? "gemountet" : (d.present ? "vorhanden" : "nicht da");
      const right = document.createElement("div");
      right.className = "d-flex align-items-center gap-1 flex-shrink-0";
      right.append(typeBadge, badge);
      const hasRegisteredConfig = knownStorageById.has(String(d.id || ""));
      if (d.is_internal || hasRegisteredConfig) {
        const manageBtn = document.createElement("button");
        manageBtn.type = "button";
        manageBtn.className = "btn btn-sm btn-outline-primary d-inline-flex align-items-center justify-content-center";
        manageBtn.title = "Dateien verwalten";
        manageBtn.setAttribute("aria-label", "Dateien verwalten");
        manageBtn.disabled = !(d.mounted && d.present);
        manageBtn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 22V6a2 2 0 0 1 2-2h5l2 2h5a2 2 0 0 1 2 2v14"/><path d="M2 22h20"/></svg>';
        manageBtn.addEventListener("click", () => run(() => openStorageFileManager(String(d.id || ""))));
        right.append(manageBtn);

        if (!d.is_internal) {
          const editBtn = document.createElement("button");
          editBtn.type = "button";
          editBtn.className = "btn btn-sm btn-outline-secondary d-inline-flex align-items-center justify-content-center";
          editBtn.title = "Laufwerk bearbeiten";
          editBtn.setAttribute("aria-label", "Laufwerk bearbeiten");
          editBtn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>';
          editBtn.addEventListener("click", () => openStorageDeviceModal(String(d.id || "")));
          right.append(editBtn);
        }
      }
      top.append(title, right);

      const fs = d.filesystem || "-";
      const mp = d.mount_path || "-";
      const total = Number(d.total_bytes || 0);
      const used = Number(d.used_bytes || 0);
      const free = Number(d.free_bytes || 0);
      const pct = Math.max(0, Math.min(100, Number(d.used_percent || 0)));

      const meta = document.createElement("div");
      meta.className = "small text-secondary mb-1";
      const src = d.source_device || "";
      meta.textContent = `FS: ${fs} | Mount: ${mp}${src ? ` | Device: ${src}` : ""}`;

      const progressWrap = document.createElement("div");
      progressWrap.className = "progress mb-1";
      const bar = document.createElement("div");
      bar.className = "progress-bar";
      bar.style.width = `${pct}%`;
      bar.textContent = `${pct}%`;
      if (pct >= 90) bar.classList.add("bg-danger");
      else if (pct >= 75) bar.classList.add("bg-warning");
      else bar.classList.add("bg-success");
      progressWrap.append(bar);

      const usage = document.createElement("div");
      usage.className = "small text-secondary";
      usage.textContent = `Gesamt: ${formatBytes(total)} | Belegt: ${formatBytes(used)} | Frei: ${formatBytes(free)}${d.uuid ? ` | UUID: ${d.uuid}` : ""}`;

      card.append(top, meta, progressWrap, usage);
      col.append(card);
      host.append(col);
    }
  }

  function renderStorageStatus(payload) {
    const data = payload.data || payload || {};
    statusDashboardState.storage = data;
    const knownList = Array.isArray(data.known) ? data.known : [];
    const drivesList = Array.isArray(data.drives) ? data.drives : [];
    knownStorageById = new Map(knownList.map((item) => [String(item.id || ""), item]));
    knownDrivesById = new Map(drivesList.map((item) => [String(item.id || ""), item]));
    q("storage-summary").textContent = `${data.known_count || 0} bekannt / ${data.new_count || 0} neu / ${data.ignored_count || 0} ignoriert`;
    renderStorageInternal(data.internal || {});
    renderStorageDrives(drivesList);
    renderStorageNew(data.new || []);
    renderStorageKnown(knownList);
    renderStorageIgnored(data.ignored || []);
    renderStatusHealthCard();
    renderStatusSoftwareSection();
    processStorageDeltas(data);
    if (storageFmState.active) {
      const selected = knownDrivesById.get(String(storageFmState.deviceId || "")) || knownStorageById.get(String(storageFmState.deviceId || ""));
      if (!selected || !selected.present || !selected.mounted) {
        closeStorageFileManager();
        toast("Dateimanager geschlossen: Laufwerk ist nicht mehr verfügbar.", "secondary");
      }
    }
  }

  function openStorageDeviceModal(deviceId) {
    const item = knownStorageById.get(String(deviceId || ""));
    if (!item) {
      toast("Laufwerkdaten nicht gefunden.", "danger");
      return;
    }
    selectedStorageDeviceId = String(item.id || "");
    q("storage-modal-title").textContent = item.name || item.label || item.uuid || item.part_uuid || "Laufwerk";
    const typeBadge = q("storage-modal-type-badge");
    typeBadge.textContent = storageTypeBadge(item.drive_type || "", false);
    const statusMeta = storageStatusBadge(item);
    const statusBadge = q("storage-modal-status-badge");
    statusBadge.className = `badge ${statusMeta.cls}`;
    statusBadge.textContent = statusMeta.text;

    q("storage-modal-ident").textContent = item.uuid || item.part_uuid || "-";
    q("storage-modal-fs").textContent = item.filesystem || "-";
    q("storage-modal-total").textContent = formatBytes(item.total_bytes || item.size_bytes || 0);
    q("storage-modal-used").textContent = formatBytes(item.used_bytes || 0);
    q("storage-modal-free").textContent = formatBytes(item.free_bytes || 0);
    q("storage-modal-mount").textContent = item.mount_path || "-";
    q("storage-modal-current-mount").textContent = item.current_mount_path || "-";
    q("storage-modal-device").textContent = item.current_device_path || item.last_seen_device_path || "-";
    q("storage-modal-enabled").textContent = item.is_enabled ? "yes" : "no";
    q("storage-modal-automount").textContent = item.auto_mount ? "yes" : "no";
    q("storage-modal-last-seen").textContent = item.last_seen_at || "-";
    q("storage-modal-error").textContent = item.last_error || "-";

    q("btn-storage-modal-mount").disabled = !item.present;
    q("btn-storage-modal-enabled").textContent = item.is_enabled ? "Deaktivieren" : "Aktivieren";
    q("btn-storage-modal-automount").textContent = item.auto_mount ? "Auto-Mount aus" : "Auto-Mount an";
    q("btn-storage-modal-format").disabled = !item.present;

    const modal = bootstrap.Modal.getOrCreateInstance(q("storageDeviceModal"));
    modal.show();
  }

  async function refreshStorageStatus() {
    const payload = await fetchJson("/api/network/storage/status");
    renderStorageStatus(payload);
    return payload;
  }

  function startStoragePolling() {
    if (storagePollHandle) {
      window.clearInterval(storagePollHandle);
    }
    storagePollHandle = window.setInterval(async () => {
      try {
        await refreshStorageStatus();
      } catch (_) {
        // ignore transient polling errors
      }
    }, 7000);
  }

  function renderApStatus(payload) {
    const data = payload.data || payload || {};
    const active = !!data.active;
    q("ap-ssid").textContent = data.ssid || "-";
    q("ap-ip").textContent = data.ip || "-";
    q("ap-portal-url").textContent = data.portal_url || (data.ip ? `http://${data.ip}` : "-");
    q("ap-profile").textContent = data.profile || "jm-hotspot";
    q("ap-clients-count").textContent = String(data.clients_count || 0);
    const badge = q("ap-active-badge");
    badge.classList.remove("text-bg-success", "text-bg-secondary");
    badge.classList.add(active ? "text-bg-success" : "text-bg-secondary");
    badge.textContent = active ? "aktiv" : "inaktiv";
  }

  function renderApClients(clients) {
    const host = q("ap-clients-list");
    clearNode(host);
    if (!Array.isArray(clients) || clients.length === 0) {
      const empty = document.createElement("div");
      empty.className = "text-secondary";
      empty.textContent = "Keine verbundenen AP-Clients.";
      host.append(empty);
      return;
    }
    for (const c of clients) {
      const row = document.createElement("div");
      row.className = "list-group-item px-0";
      const top = document.createElement("div");
      top.className = "d-flex justify-content-between align-items-center gap-2";
      const mac = document.createElement("strong");
      mac.textContent = c.mac || "-";
      const status = document.createElement("span");
      status.className = `badge ${c.status === "connected" ? "text-bg-success" : "text-bg-secondary"}`;
      status.textContent = c.status || "unknown";
      top.append(mac, status);

      const meta = document.createElement("div");
      meta.className = "text-secondary";
      const ip = c.ip || "-";
      const hostname = c.hostname || "-";
      const lastSeen = c.last_seen || "-";
      meta.textContent = `IP: ${ip} | Host: ${hostname} | Last seen: ${lastSeen}`;
      row.append(top, meta);
      host.append(row);
    }
  }

  async function refreshApStatus() {
    const payload = await fetchJson("/api/network/ap/status");
    renderApStatus(payload);
    return payload;
  }

  async function refreshApClients() {
    const payload = await fetchJson("/api/network/ap/clients");
    const data = payload.data || {};
    const clients = Array.isArray(data.clients) ? data.clients : [];
    renderApClients(clients);

    const currentConnected = new Set(
      clients
        .filter((c) => (c.status || "") === "connected" && c.mac)
        .map((c) => String(c.mac).toLowerCase()),
    );

    if (apClientsInitialized) {
      for (const mac of currentConnected) {
        if (!apKnownConnectedMacs.has(mac)) {
          const client = clients.find((c) => String(c.mac || "").toLowerCase() === mac) || {};
          const suffix = client.ip ? ` (${client.ip})` : "";
          toast(`Neuer AP-Client verbunden: ${mac}${suffix}`, "success");
        }
      }
    }
    apKnownConnectedMacs = currentConnected;
    apClientsInitialized = true;
    return payload;
  }

  async function toggleAp(enabled) {
    await fetchJson("/api/network/ap/toggle", { method: "POST", body: { enabled, ifname: "wlan0", profile: "jm-hotspot" } });
    await refreshApStatus();
    await refreshApClients();
    toast(enabled ? "AP wurde eingeschaltet." : "AP wurde ausgeschaltet.", "success");
  }

  function startApPolling() {
    if (apPollHandle) {
      window.clearInterval(apPollHandle);
    }
    apPollHandle = window.setInterval(async () => {
      try {
        await refreshApStatus();
        await refreshApClients();
      } catch (_) {
        // ignore transient polling failures
      }
    }, 5000);
  }

  function collapseMeshScanNetworks(networks) {
    if (!Array.isArray(networks)) return [];
    const grouped = new Map();

    for (const raw of networks) {
      const item = raw && typeof raw === "object" ? raw : {};
      const ssid = String(item.ssid || "").trim();
      const hidden = !ssid || ssid === "<hidden>";
      const key = hidden ? `hidden:${String(item.bssid || "").trim().toLowerCase() || Math.random().toString(36).slice(2)}` : `ssid:${ssid.toLowerCase()}`;
      const signal = Number.isFinite(Number(item.signal)) ? Number(item.signal) : 0;

      if (!grouped.has(key)) {
        grouped.set(key, {
          ...item,
          ssid: ssid || "<hidden>",
          signal,
          in_use: !!item.in_use,
          mesh_nodes: 1,
          _securities: new Set([(item.security || "OPEN").toString().trim() || "OPEN"]),
        });
        continue;
      }

      const current = grouped.get(key);
      current.mesh_nodes += 1;
      current._securities.add((item.security || "OPEN").toString().trim() || "OPEN");

      const better =
        (!!item.in_use && !current.in_use) ||
        (!!item.in_use === !!current.in_use && signal > Number(current.signal || 0));
      if (better) {
        grouped.set(key, {
          ...current,
          ...item,
          ssid: ssid || "<hidden>",
          signal,
          in_use: !!item.in_use,
          mesh_nodes: current.mesh_nodes,
          _securities: current._securities,
        });
      }
    }

    const result = [];
    for (const entry of grouped.values()) {
      result.push({
        ...entry,
        security: Array.from(entry._securities).filter(Boolean).join(" "),
      });
    }
    result.sort((a, b) => {
      if (!!a.in_use !== !!b.in_use) return a.in_use ? -1 : 1;
      return Number(b.signal || 0) - Number(a.signal || 0);
    });
    return result;
  }

  function renderWifiScan(networks) {
    const deduped = collapseMeshScanNetworks(networks);
    const host = q("wifi-scan-list");
    clearNode(host);
    if (!deduped.length) {
      const empty = document.createElement("div");
      empty.className = "text-secondary";
      empty.textContent = "Keine WLAN Netze gefunden.";
      host.append(empty);
      return;
    }
    for (const item of deduped) {
      const row = document.createElement("div");
      row.className = "list-group-item px-0";
      const top = document.createElement("div");
      top.className = "d-flex justify-content-between align-items-center gap-2";
      const title = document.createElement("strong");
      title.textContent = item.ssid || "<hidden>";
      const badge = document.createElement("span");
      badge.className = `badge ${item.in_use ? "text-bg-success" : "text-bg-secondary"}`;
      badge.textContent = item.in_use ? "connected" : `${item.signal || 0}%`;
      top.append(title, badge);

      const meta = document.createElement("div");
      meta.className = "text-secondary mb-2";
      const meshInfo = Number(item.mesh_nodes || 1) > 1 ? ` | Mesh-Knoten: ${Number(item.mesh_nodes || 1)}` : "";
      meta.textContent = `Security: ${item.security || "OPEN"}${meshInfo}`;

      const actions = document.createElement("div");
      actions.className = "d-flex gap-2";
      const inlineConnect = document.createElement("div");
      inlineConnect.className = "mt-2 d-none";
      const inlineRow = document.createElement("div");
      inlineRow.className = "d-flex flex-wrap gap-2";
      const inlinePw = document.createElement("input");
      inlinePw.type = "password";
      inlinePw.className = "form-control form-control-sm";
      inlinePw.style.maxWidth = "320px";
      inlinePw.placeholder = "Passwort (leer für OPEN/WPS)";
      const inlineSubmit = document.createElement("button");
      inlineSubmit.className = "btn btn-primary btn-sm";
      inlineSubmit.textContent = "Jetzt verbinden";
      const inlineCancel = document.createElement("button");
      inlineCancel.className = "btn btn-outline-secondary btn-sm";
      inlineCancel.textContent = "Abbrechen";
      inlineRow.append(inlinePw, inlineSubmit, inlineCancel);
      inlineConnect.append(inlineRow);
      const selectBtn = document.createElement("button");
      selectBtn.className = "btn btn-outline-dark btn-sm";
      selectBtn.textContent = "WPS Ziel";
      selectBtn.addEventListener("click", () => {
        setWpsTarget({ ssid: item.ssid || "", bssid: item.bssid || "" });
        toast(`WPS Ziel gesetzt: ${item.ssid || "unbekannt"}`, "secondary");
      });
      const connectBtn = document.createElement("button");
      connectBtn.className = "btn btn-outline-primary btn-sm";
      connectBtn.textContent = "Verbinden";
      connectBtn.addEventListener("click", () => {
        if (!item.ssid || item.ssid === "<hidden>") {
          toast("Hidden SSID bitte manuell hinzufügen.", "secondary");
          return;
        }
        inlineConnect.classList.remove("d-none");
        inlinePw.focus();
      });
      inlineSubmit.addEventListener("click", () => run(async () => {
        await connectSsid(item.ssid || "", inlinePw.value || "");
        inlineConnect.classList.add("d-none");
        inlinePw.value = "";
      }));
      inlineCancel.addEventListener("click", () => {
        inlineConnect.classList.add("d-none");
        inlinePw.value = "";
      });
      const wpsBtn = document.createElement("button");
      wpsBtn.className = "btn btn-outline-secondary btn-sm";
      wpsBtn.textContent = "WPS";
      wpsBtn.addEventListener("click", () => run(() => startWps({ ssid: item.ssid || "", bssid: "" })));
      actions.append(selectBtn, connectBtn, wpsBtn);
      row.append(top, meta, actions, inlineConnect);
      host.append(row);
    }
  }

  async function refreshWifiScan() {
    const payload = await fetchJson("/api/wifi/scan");
    const data = payload.data || {};
    renderWifiScan(data.networks || []);
    return data;
  }

  async function connectSsid(ssid, password = "") {
    if (!ssid || ssid === "<hidden>") {
      toast("Hidden SSID bitte manuell hinzufügen.", "secondary");
      return;
    }
    let pw = password;
    if (!pw) {
      pw = window.prompt(`Passwort für "${ssid}" (leer lassen für open/WPS):`, "") || "";
    }
    const payload = await fetchJson("/api/wifi/connect", {
      method: "POST",
      body: { ssid, password: pw, ifname: "wlan0", hidden: false },
      timeoutMs: 30000,
    });
    await refreshNetwork();
    await refreshWifiProfiles();
    toast(`WLAN verbunden/angefragt: ${payload.data?.ssid || ssid}`, "success");
  }

  function renderWifiProfiles(payload) {
    const data = payload.data || {};
    wifiProfilesState = data;
    const host = q("wifi-profiles-list");
    clearNode(host);
    const unmanagedFallback = (data.unmanaged || []).map((item) => ({
      ssid: item.name || "",
      priority: Number.isFinite(item.priority) ? item.priority : 0,
      autoconnect: !!item.autoconnect,
      source: "nm",
      nm: { uuid: item.uuid || "" },
    })).filter((item) => item.ssid);
    const profiles = (data.profiles && data.profiles.length) ? data.profiles : ((data.configured && data.configured.length) ? data.configured : unmanagedFallback);
    const preferred = data.preferred_ssid || "";
    const last = data.last_wifi_ssid || "";
    if (!profiles.length) {
      const empty = document.createElement("div");
      empty.className = "text-secondary";
      empty.textContent = "Keine WLAN Profile gespeichert.";
      host.append(empty);
      return;
    }
    for (const item of profiles) {
      const ssid = item.ssid || "";
      const row = document.createElement("div");
      row.className = "list-group-item px-0";
      const top = document.createElement("div");
      top.className = "d-flex justify-content-between align-items-center gap-2";
      const title = document.createElement("strong");
      title.textContent = ssid;
      const info = document.createElement("span");
      info.className = "text-secondary";
      const flags = [];
      if (preferred && ssid === preferred) flags.push("preferred");
      if (last && ssid === last) flags.push("last");
      if (item.source && item.source !== "config+nm") flags.push(item.source);
      info.textContent = `prio=${item.priority ?? 0} auto=${item.autoconnect ? "yes" : "no"} ${flags.join(" ")}`.trim();
      top.append(title, info);

      const actions = document.createElement("div");
      actions.className = "d-flex gap-2 mt-2";
      const upBtn = document.createElement("button");
      upBtn.className = "btn btn-outline-primary btn-sm";
      upBtn.textContent = "Verbinden";
      upBtn.addEventListener("click", () => run(() => wifiProfileUp(ssid, item?.nm?.uuid || "")));
      const wpsBtn = document.createElement("button");
      wpsBtn.className = "btn btn-outline-secondary btn-sm";
      wpsBtn.textContent = "WPS";
      wpsBtn.addEventListener("click", () => run(async () => {
        setWpsTarget({ ssid, bssid: "" });
        await startWps({ ssid, bssid: "" });
      }));
      const prefBtn = document.createElement("button");
      prefBtn.className = "btn btn-outline-secondary btn-sm";
      prefBtn.textContent = "Prefer";
      prefBtn.addEventListener("click", () => run(() => wifiProfilePrefer(ssid)));
      const delBtn = document.createElement("button");
      delBtn.className = "btn btn-outline-danger btn-sm";
      delBtn.textContent = "Löschen";
      delBtn.addEventListener("click", () => run(() => wifiProfileDelete(ssid, item?.nm?.uuid || "")));
      if (connectivitySetupState.active) {
        actions.append(upBtn, wpsBtn);
      } else {
        actions.append(upBtn, wpsBtn, prefBtn, delBtn);
      }
      row.append(top, actions);
      host.append(row);
    }
  }

  async function refreshWifiProfiles() {
    const payload = await fetchJson("/api/wifi/profiles");
    renderWifiProfiles(payload);
    return payload;
  }

  async function wifiProfileUp(ssid, uuid = "") {
    await fetchJson("/api/wifi/profiles/up", { method: "POST", body: { ssid, uuid } });
    await refreshNetwork();
    await refreshWifiProfiles();
    toast(`Profil verbunden: ${ssid}`, "success");
  }

  async function wifiProfilePrefer(ssid) {
    await fetchJson("/api/wifi/profiles/prefer", { method: "POST", body: { ssid } });
    await refreshWifiProfiles();
    toast(`Preferred gesetzt: ${ssid}`, "success");
  }

  async function wifiProfileDelete(ssid, uuid = "") {
    if (!window.confirm(`Profil wirklich löschen? (${ssid})`)) return;
    await fetchJson("/api/wifi/profiles/delete", { method: "POST", body: { ssid, uuid } });
    await refreshWifiProfiles();
    toast(`Profil gelöscht: ${ssid}`, "success");
  }

  async function wifiProfilesApply() {
    const payload = await fetchJson("/api/wifi/profiles/apply", { method: "POST", timeoutMs: 45000 });
    const connected = payload.data?.connected_ssid || "";
    await refreshNetwork();
    await refreshWifiProfiles();
    if (connected) {
      toast(`Profile angewendet, verbunden mit: ${connected}`, "success");
    } else {
      toast("Profile angewendet, keine aktive Verbindung erkannt.", "secondary");
    }
  }

  async function wifiProfilesAddManual() {
    const ssid = (q("wifi-manual-ssid").value || "").trim();
    const password = q("wifi-manual-password").value || "";
    const hidden = !!q("wifi-manual-hidden").checked;
    if (!ssid) {
      toast("SSID fehlt.", "danger");
      return;
    }
    const body = {
      ssid,
      password,
      priority: 80,
      autoconnect: true,
      ifname: "wlan0",
      hidden,
    };
    const payload = await fetchJson("/api/wifi/profiles/add", { method: "POST", body, timeoutMs: 30000 });
    q("wifi-manual-password").value = "";
    q("wifi-manual-hidden").checked = false;
    await refreshNetwork();
    await refreshWifiProfiles();
    const warning = payload.data?.warning;
    if (warning) {
      toast(warning, "secondary");
    } else {
      toast(`Profil gespeichert: ${ssid}`, "success");
    }
  }

  function renderWifiLogs(events) {
    const logEl = q("wifi-event-log");
    if (!Array.isArray(events) || !events.length) {
      logEl.textContent = "-";
      return;
    }
    const lines = events.map((e) => {
      const ts = e.ts || "";
      const lvl = (e.level || "info").toUpperCase();
      const msg = e.message || "";
      return `[${ts}] ${lvl} ${msg}`;
    });
    logEl.textContent = lines.join("\n");
    logEl.scrollTop = logEl.scrollHeight;
  }

  async function refreshWifiLogs() {
    const payload = await fetchJson("/api/network/wifi/logs?limit=120");
    const data = payload.data || {};
    renderWifiLogs(data.events || []);
    return data;
  }

  async function refreshWpsStatus() {
    const payload = await fetchJson("/api/network/wifi/wps/status");
    const data = payload.data || {};
    const wps = data.wps || {};
    q("wifi-wps-phase").textContent = `${wps.phase || "idle"} - ${wps.phase_message || ""}`.trim();
    return data;
  }

  function startWpsPolling() {
    if (wpsPollHandle) {
      window.clearInterval(wpsPollHandle);
    }
    let loops = 0;
    wpsPollHandle = window.setInterval(async () => {
      loops += 1;
      try {
        const data = await refreshWpsStatus();
        await refreshNetwork();
        await refreshWifiLogs();
        const phase = ((data || {}).wps || {}).phase || "";
        if (phase === "connected" || phase === "timeout" || loops > 60) {
          window.clearInterval(wpsPollHandle);
          wpsPollHandle = null;
        }
      } catch (_) {
        if (loops > 10) {
          window.clearInterval(wpsPollHandle);
          wpsPollHandle = null;
        }
      }
    }, 3000);
  }

  async function panelCheckLink() {
    await fetchJson("/api/panel/link-status");
    await refreshStatus();
    toast("Panel link status refreshed", "success");
  }

  async function panelUnlink() {
    await fetchJson("/api/panel/unlink", { method: "POST" });
    await refreshStatus();
    toast("Panel unlinked", "success");
  }

  async function panelPing() {
    const adminBaseUrl = requireAdminBaseUrl();
    await fetchJson("/api/panel/ping", {
      method: "POST",
      body: { admin_base_url: adminBaseUrl },
    });
    await refreshStatus();
    toast("Panel ping completed", "success");
  }

  async function panelRegister() {
    const adminBaseUrl = requireAdminBaseUrl();
    await fetchJson("/api/panel/register", {
      method: "POST",
      body: {
        admin_base_url: adminBaseUrl,
        registration_token: els.regToken.value || "",
      },
    });
    await refreshStatus();
    toast("Panel register completed", "success");
  }

  async function panelSyncCheck() {
    const adminBaseUrl = requireAdminBaseUrl();
    try {
      const payload = await fetchJson("/api/panel/sync-status", {
        method: "POST",
        body: { admin_base_url: adminBaseUrl },
      });
      panelSyncState.lastCheckAt = new Date().toLocaleString();
      const adminResponse = payload.response || {};
      const syncData = adminResponse.data || {};
      if (payload.panel_device_flags && statusDashboardState.status && statusDashboardState.status.config) {
        statusDashboardState.status.config.panel_device_flags = payload.panel_device_flags;
        renderHeroPanelFlags(statusDashboardState.status.config);
      }
      renderPanelSyncStatus(syncData, false);
      toast(syncData.ok ? "Admin-Sync vollständig" : "Admin-Sync unvollständig", syncData.ok ? "success" : "warning");
    } catch (err) {
      panelSyncState.lastCheckAt = new Date().toLocaleString();
      renderPanelSyncStatus(null, true);
      throw err;
    }
  }

  async function panelSyncNow() {
    const adminBaseUrl = requireAdminBaseUrl();
    await fetchJson("/api/panel/sync-now", {
      method: "POST",
      body: { admin_base_url: adminBaseUrl },
    });
    await refreshStatus();
    await panelSyncCheck();
    toast("Daten an Adminpanel nachgemeldet", "success");
  }

  async function panelTestUrl() {
    const adminBaseUrl = requireAdminBaseUrl();
    await fetchJson("/api/panel/test-url", {
      method: "POST",
      body: { url: adminBaseUrl },
    });
    toast("Panel URL saved", "success");
  }

  async function pullPlan() {
    const adminBaseUrl = requireAdminBaseUrl();
    await fetchJson("/api/plan/pull", {
      method: "POST",
      body: {
        admin_base_url: adminBaseUrl,
        deviceSlug: els.deviceSlug.value || "",
        streamSlug: els.streamSlug.value || "",
      },
    });
    await refreshStatus();
    toast("Plan pulled", "success");
  }

  function renderStreamOverview(payload) {
    const data = payload || {};
    const status = data.status || {};
    const streams = Array.isArray(data.streams) ? data.streams : [];
    const fetchError = String(data.fetch_error || "").trim();
    const storage = data.storage || {};
    const storageError = String(data.storage_error || "").trim();
    const player = data.player || {};

    const select = q("stream-select");
    if (select) {
      select.innerHTML = "";
      if (streams.length === 0) {
        const opt = document.createElement("option");
        opt.value = "";
        opt.textContent = "Keine Streams verfügbar";
        select.appendChild(opt);
      } else {
        streams.forEach((stream) => {
          const opt = document.createElement("option");
          opt.value = String(stream.slug || "");
          opt.textContent = `${stream.name || stream.slug || "Stream"} (${stream.slug || "-"})`;
          if (stream.isSelected) {
            opt.selected = true;
          }
          select.appendChild(opt);
        });
      }
    }

    const selected = String(status.selected_stream_slug || data.admin_selected_stream_slug || "");
    q("stream-selected-slug").textContent = selected || "-";
    q("stream-manifest-version").textContent = String(status.stream_manifest_version || "-");
    q("stream-asset-count").textContent = String(status.stream_asset_count || 0);
    q("stream-last-sync").textContent = String(status.stream_last_sync_at || "-");
    q("stream-list-meta").textContent = `Streams: ${streams.length} · Admin: ${status.admin_base_url || "-"}`;

    const storageText = storage && storage.current_path
      ? `${storage.label || storage.device_id || "Storage"} · ${storage.current_path}`
      : "-";
    q("stream-storage-target").textContent = storageText;

    const fetchErrorEl = q("stream-fetch-error");
    if (fetchError) {
      fetchErrorEl.classList.remove("d-none");
      fetchErrorEl.textContent = fetchError;
    } else {
      fetchErrorEl.classList.add("d-none");
      fetchErrorEl.textContent = "";
    }

    const syncError = String(status.stream_sync_error || storageError || "");
    const syncErrorEl = q("stream-sync-error");
    if (syncError) {
      syncErrorEl.classList.remove("d-none");
      syncErrorEl.textContent = syncError;
    } else {
      syncErrorEl.classList.add("d-none");
      syncErrorEl.textContent = "";
    }

    const playerStatusEl = q("stream-player-status");
    if (playerStatusEl) {
      if (player && player.error) {
        playerStatusEl.textContent = `Status: Fehler (${player.error})`;
      } else if (player && typeof player.active !== "undefined") {
        playerStatusEl.textContent = `Status: ${player.active ? "aktiv" : "inaktiv"} (${player.substate || "-"})`;
      } else {
        playerStatusEl.textContent = "Status: -";
      }
    }
  }

  async function refreshStreamOverview() {
    const payload = await fetchJson("/api/stream/overview");
    renderStreamOverview(payload);
  }

  async function saveSelectedStream() {
    const select = q("stream-select");
    const streamSlug = String(select?.value || "").trim();
    if (!streamSlug) {
      throw new Error("Bitte zuerst einen Stream auswählen.");
    }
    await fetchJson("/api/stream/select", {
      method: "POST",
      body: { streamSlug },
    });
    await refreshStreamOverview();
    await refreshStatus();
    toast("Stream-Auswahl gespeichert", "success");
  }

  async function syncSelectedStream() {
    const select = q("stream-select");
    const streamSlug = String(select?.value || q("stream-selected-slug")?.textContent || "").trim();
    if (!streamSlug || streamSlug === "-") {
      throw new Error("Kein Stream ausgewählt.");
    }
    const response = await fetchJson("/api/stream/sync", {
      method: "POST",
      body: { streamSlug },
      timeoutMs: 120000,
    });
    await refreshStreamOverview();
    await refreshStatus();
    const count = Number((response || {}).asset_count || 0);
    toast(`Sync abgeschlossen (${count} Assets)`, "success");
  }

  async function refreshPlayerStatus() {
    const payload = await fetchJson("/api/stream/player/status");
    const player = payload.player || {};
    const playerStatusEl = q("stream-player-status");
    if (playerStatusEl) {
      if (player && player.error) {
        playerStatusEl.textContent = `Status: Fehler (${player.error})`;
      } else if (typeof player.active !== "undefined") {
        playerStatusEl.textContent = `Status: ${player.active ? "aktiv" : "inaktiv"} (${player.substate || "-"})`;
      } else {
        playerStatusEl.textContent = "Status: -";
      }
    }
  }

  function renderStreamAudioStatus(payload) {
    const data = (payload && payload.data && typeof payload.data === "object") ? payload.data : {};
    const health = (data.health && typeof data.health === "object") ? data.health : {};
    const state = String(data.state || "-");
    const sourceType = String(data.source_type || "-");
    const source = String(data.source || "-");
    const volume = Number.isFinite(Number(data.volume)) ? Number(data.volume) : "-";
    const output = String(data.output || "-");
    const healthText = String(health.status || "-");
    const statusEl = q("stream-audio-status");
    if (statusEl) {
      statusEl.textContent = `Status: ${state} | source: ${sourceType} | output: ${output} | volume: ${volume} | health: ${healthText} | ${source}`;
    }
    const volumeInput = q("stream-audio-volume");
    if (volumeInput && Number.isFinite(Number(data.volume))) {
      volumeInput.value = String(Number(data.volume));
    }
    const vol = Number(data.volume);
    if (Number.isFinite(vol)) {
      const clamped = Math.max(0, Math.min(100, Math.round(vol)));
      const valueEl = q("stream-audio-volume-value");
      if (valueEl) valueEl.textContent = String(clamped);
      streamAudioMuted = clamped === 0;
      if (clamped > 0) {
        streamAudioLastNonZeroVolume = clamped;
      }
      const muteBtn = q("btn-stream-audio-mute");
      if (muteBtn) {
        muteBtn.textContent = streamAudioMuted ? "Ton an" : "Stumm";
      }
    }
  }

  async function refreshStreamAudioStatus() {
    const payload = await fetchJson("/api/stream/player/audio/status", { cache: "no-store" });
    renderStreamAudioStatus(payload);
    return payload;
  }

  async function refreshStreamAudioFiles() {
    const payload = await fetchJson("/api/stream/player/audio/files", { cache: "no-store" });
    const files = Array.isArray(payload.files) ? payload.files : [];
    const root = String(payload.root || "-");
    q("stream-audio-root").textContent = root;
    const select = q("stream-audio-file-select");
    if (select) {
      select.innerHTML = "";
      if (files.length === 0) {
        const opt = document.createElement("option");
        opt.value = "";
        opt.textContent = "Keine Audio-Dateien gefunden";
        select.appendChild(opt);
      } else {
        for (const file of files) {
          const opt = document.createElement("option");
          opt.value = String(file.path || "");
          opt.textContent = `${String(file.relative_path || file.name || "audio")} (${Math.round((Number(file.size_bytes || 0) / 1024))} KB)`;
          select.appendChild(opt);
        }
      }
    }
    const firstPath = files.length > 0 ? String(files[0].path || "") : "";
    q("stream-audio-file-path").value = firstPath;
    return payload;
  }

  async function streamAudioPlayFile() {
    const selected = String(q("stream-audio-file-select")?.value || "").trim();
    const path = String(q("stream-audio-file-path")?.value || "").trim() || selected;
    if (!path) throw new Error("Bitte Audio-Datei auswählen oder Pfad eintragen.");
    await fetchJson("/api/stream/player/audio/play-file", { method: "POST", body: { path }, timeoutMs: 12000 });
    await refreshStreamAudioStatus();
    toast("Audio-Datei gestartet", "success");
  }

  async function streamAudioPlayStream() {
    const url = String(q("stream-audio-stream-url")?.value || "").trim();
    if (!url) throw new Error("Bitte Webstream-URL eingeben.");
    await fetchJson("/api/stream/player/audio/play-stream", { method: "POST", body: { url }, timeoutMs: 12000 });
    await refreshStreamAudioStatus();
    toast("Webstream gestartet", "success");
  }

  async function streamAudioAction(action) {
    await fetchJson(`/api/stream/player/audio/${action}`, { method: "POST", timeoutMs: 10000 });
    await refreshStreamAudioStatus();
    toast(`Audio ${action}`, "success");
  }

  async function streamAudioSetVolume(options = {}) {
    const notify = options.notify !== false;
    const refresh = options.refresh !== false;
    const raw = String(q("stream-audio-volume")?.value || "").trim();
    const volume = Number(raw);
    if (!Number.isFinite(volume)) throw new Error("Lautstärke muss eine Zahl sein.");
    const clamped = Math.max(0, Math.min(100, Math.round(volume)));
    await fetchJson("/api/stream/player/audio/volume", { method: "POST", body: { volume: clamped } });
    const valueEl = q("stream-audio-volume-value");
    if (valueEl) valueEl.textContent = String(clamped);
    if (clamped > 0) {
      streamAudioLastNonZeroVolume = clamped;
    }
    streamAudioMuted = clamped === 0;
    const muteBtn = q("btn-stream-audio-mute");
    if (muteBtn) {
      muteBtn.textContent = streamAudioMuted ? "Ton an" : "Stumm";
    }
    if (refresh) {
      await refreshStreamAudioStatus();
    }
    if (notify) {
      toast("Lautstärke gesetzt", "success");
    }
  }

  function scheduleStreamAudioVolumeSet() {
    if (streamAudioVolumeDebounceHandle) {
      window.clearTimeout(streamAudioVolumeDebounceHandle);
    }
    streamAudioVolumeDebounceHandle = window.setTimeout(() => {
      run(() => streamAudioSetVolume({ notify: false, refresh: false }));
    }, 120);
  }

  async function toggleStreamAudioMute() {
    const slider = q("stream-audio-volume");
    const current = Number(slider?.value || 0);
    if (!Number.isFinite(current)) {
      throw new Error("Lautstärke ist ungültig.");
    }
    const target = current <= 0 ? Math.max(1, streamAudioLastNonZeroVolume || 65) : 0;
    if (slider) {
      slider.value = String(target);
    }
    await streamAudioSetVolume({ notify: true, refresh: true });
  }

  async function loadPlayerRepoConfig() {
    const payload = await fetchJson("/api/stream/player/repo", { timeoutMs: 10000 });
    const cfg = payload.config || {};
    q("stream-player-repo-dir").value = String(cfg.player_repo_link || cfg.player_repo_dir || "");
    q("stream-player-service-name").value = String(cfg.player_service_name || "joormann-media-deviceplayer.service");
    q("stream-player-service-user").value = String(cfg.player_service_user || "");
  }

  async function savePlayerRepoConfig() {
    const player_repo_link = String(q("stream-player-repo-dir")?.value || "").trim();
    const player_service_name = String(q("stream-player-service-name")?.value || "").trim() || "joormann-media-deviceplayer.service";
    const player_service_user = String(q("stream-player-service-user")?.value || "").trim();
    await fetchJson("/api/stream/player/repo", {
      method: "POST",
      body: { player_repo_link, player_service_name, player_service_user },
      timeoutMs: 12000,
    });
    toast("Player-Repo Link gespeichert", "success");
  }

  function overlayNum(id, fallback = 0) {
    const raw = String(q(id)?.value || "").trim();
    const num = Number(raw);
    return Number.isFinite(num) ? num : fallback;
  }

  function overlayStr(id, fallback = "") {
    const raw = String(q(id)?.value || "").trim();
    return raw || fallback;
  }

  function overlayChecked(id, fallback = true) {
    const el = q(id);
    return el ? !!el.checked : fallback;
  }

  function renderOverlayState(payload) {
    const data = (payload && payload.data && typeof payload.data === "object") ? payload.data : {};
    const path = String(payload?.path || "-");
    const pretty = JSON.stringify(data, null, 2);
    const pathEl = q("overlay-state-path");
    if (pathEl) pathEl.textContent = path;
    const pre = q("overlay-state-json");
    if (pre) pre.textContent = pretty || "{}";
  }

  async function refreshOverlayState() {
    const payload = await fetchJson("/api/player/overlay/state");
    renderOverlayState(payload);
    return payload;
  }

  async function saveOverlayFlash() {
    const payload = {
      id: overlayStr("overlay-flash-id", ""),
      enabled: overlayChecked("overlay-flash-enabled", true),
      title: overlayStr("overlay-flash-title", ""),
      message: overlayStr("overlay-flash-message", ""),
      durationMs: overlayNum("overlay-flash-duration", 5000),
      position: overlayStr("overlay-flash-position", "top"),
      rotation: overlayNum("overlay-flash-rotation", 0),
      backgroundColor: overlayStr("overlay-flash-bg", "#111111"),
      textColor: overlayStr("overlay-flash-fg", "#ffffff"),
      accentColor: overlayStr("overlay-flash-accent", "#0d6efd"),
      fontSize: overlayNum("overlay-flash-font", 32),
      padding: overlayNum("overlay-flash-padding", 24),
      opacity: overlayNum("overlay-flash-opacity", 0.95),
    };
    const response = await fetchJson("/api/player/overlay/flash", { method: "POST", body: payload });
    if (!payload.id && response?.item?.id) {
      const idEl = q("overlay-flash-id");
      if (idEl) idEl.value = String(response.item.id);
    }
    await refreshOverlayState();
    toast("Flash gespeichert", "success");
  }

  async function saveOverlayTicker() {
    const payload = {
      id: overlayStr("overlay-ticker-id", ""),
      enabled: overlayChecked("overlay-ticker-enabled", true),
      text: overlayStr("overlay-ticker-text", ""),
      position: overlayStr("overlay-ticker-position", "bottom"),
      rotation: overlayNum("overlay-ticker-rotation", 0),
      speedPxPerSecond: overlayNum("overlay-ticker-speed", 120),
      height: overlayNum("overlay-ticker-height", 72),
      paddingX: overlayNum("overlay-ticker-padding-x", 24),
      backgroundColor: overlayStr("overlay-ticker-bg", "#000000"),
      textColor: overlayStr("overlay-ticker-fg", "#ffffff"),
      fontSize: overlayNum("overlay-ticker-font", 34),
      opacity: overlayNum("overlay-ticker-opacity", 0.90),
    };
    const response = await fetchJson("/api/player/overlay/ticker", { method: "POST", body: payload });
    if (!payload.id && response?.item?.id) {
      const idEl = q("overlay-ticker-id");
      if (idEl) idEl.value = String(response.item.id);
    }
    await refreshOverlayState();
    toast("Ticker gespeichert", "success");
  }

  async function saveOverlayPopup() {
    const payload = {
      id: overlayStr("overlay-popup-id", ""),
      enabled: overlayChecked("overlay-popup-enabled", true),
      title: overlayStr("overlay-popup-title", ""),
      message: overlayStr("overlay-popup-message", ""),
      durationMs: overlayNum("overlay-popup-duration", 8000),
      position: overlayStr("overlay-popup-position", "center"),
      imagePath: overlayStr("overlay-popup-image-path", ""),
      backgroundColor: overlayStr("overlay-popup-bg", "#ffffff"),
      textColor: overlayStr("overlay-popup-fg", "#111111"),
      accentColor: overlayStr("overlay-popup-accent", "#dc3545"),
      width: overlayNum("overlay-popup-width", 800),
      height: overlayNum("overlay-popup-height", 420),
      padding: overlayNum("overlay-popup-padding", 24),
      opacity: overlayNum("overlay-popup-opacity", 1.0),
    };
    const response = await fetchJson("/api/player/overlay/popup", { method: "POST", body: payload });
    if (!payload.id && response?.item?.id) {
      const idEl = q("overlay-popup-id");
      if (idEl) idEl.value = String(response.item.id);
    }
    await refreshOverlayState();
    toast("Popup gespeichert", "success");
  }

  async function clearOverlayCategory(category) {
    await fetchJson("/api/player/overlay/clear", {
      method: "POST",
      body: { category },
    });
    await refreshOverlayState();
    toast(`${category} geleert`, "success");
  }

  async function resetOverlayState() {
    const confirmed = window.confirm("Alle Overlay-Einträge wirklich zurücksetzen?");
    if (!confirmed) return;
    await fetchJson("/api/player/overlay/reset", { method: "POST" });
    await refreshOverlayState();
    toast("Overlay-Status zurückgesetzt", "success");
  }

  function renderStreamPlayerUpdateStatus(data) {
    const logEl = q("stream-player-update-log");
    if (!logEl) return;
    const status = String((data || {}).status || "unknown");
    const lines = [
      `status: ${status}`,
      `success: ${String(!!(data || {}).success)}`,
      `message: ${(data || {}).message || "-"}`,
      `job_id: ${(data || {}).job_id || "-"}`,
      `repo: ${(data || {}).repo_dir || "-"}`,
      `user: ${(data || {}).service_user || "-"}`,
      `service: ${(data || {}).service_name || "-"}`,
      `git_status: ${(data || {}).git_status || "-"}`,
      `commit: ${((data || {}).before_commit || "-")} -> ${((data || {}).after_commit || "-")}`,
      `started_at: ${(data || {}).started_at || "-"}`,
      `finished_at: ${(data || {}).finished_at || "-"}`,
      "",
      "log:",
      (data || {}).log || "-",
    ];
    logEl.textContent = lines.join("\n");
    logEl.scrollTop = logEl.scrollHeight;
  }

  async function fetchStreamPlayerUpdateStatus(jobId = "") {
    const query = jobId ? `?job_id=${encodeURIComponent(jobId)}` : "";
    const payload = await fetchJson(`/api/stream/player/install-update/status${query}`, { timeoutMs: 8000, cache: "no-store" });
    return payload.data || {};
  }

  async function pollStreamPlayerUpdateStatus(jobId = "") {
    if (streamPlayerUpdatePollHandle) {
      window.clearInterval(streamPlayerUpdatePollHandle);
      streamPlayerUpdatePollHandle = null;
    }
    const first = await fetchStreamPlayerUpdateStatus(jobId);
    renderStreamPlayerUpdateStatus(first);
    const status = String(first.status || "").toLowerCase();
    if (["running", "restarting"].includes(status)) {
      streamPlayerUpdatePollHandle = window.setInterval(async () => {
        try {
          const data = await fetchStreamPlayerUpdateStatus(streamPlayerUpdateJobId);
          renderStreamPlayerUpdateStatus(data);
          const nextStatus = String(data.status || "").toLowerCase();
          if (!["running", "restarting"].includes(nextStatus)) {
            if (streamPlayerUpdatePollHandle) {
              window.clearInterval(streamPlayerUpdatePollHandle);
              streamPlayerUpdatePollHandle = null;
            }
          }
        } catch (_) {
          if (streamPlayerUpdatePollHandle) {
            window.clearInterval(streamPlayerUpdatePollHandle);
            streamPlayerUpdatePollHandle = null;
          }
        }
      }, 3500);
    }
  }

  async function startStreamPlayerInstallUpdate() {
    const player_repo_link = String(q("stream-player-repo-dir")?.value || "").trim();
    const player_service_name = String(q("stream-player-service-name")?.value || "").trim() || "joormann-media-deviceplayer.service";
    const player_service_user = String(q("stream-player-service-user")?.value || "").trim();
    if (!player_repo_link) {
      throw new Error("Bitte zuerst Player-Repo Link/Pfad setzen.");
    }
    const payload = await fetchJson("/api/stream/player/install-update", {
      method: "POST",
      body: { player_repo_link, player_service_name, player_service_user },
      timeoutMs: 20000,
    });
    const data = payload.data || {};
    streamPlayerUpdateJobId = String(data.job_id || "").trim();
    await pollStreamPlayerUpdateStatus(streamPlayerUpdateJobId);
    toast("Player Install/Update gestartet", "success");
  }

  async function playerAction(action) {
    await fetchJson(`/api/stream/player/${action}`, { method: "POST" });
    await refreshPlayerStatus();
    toast(`Player ${action}`, "success");
  }

  async function refreshFingerprint() {
    await fetchJson("/api/status/fingerprint/refresh", { method: "POST" });
    toast("Fingerprint refreshed", "success");
  }

  async function rebuildFingerprintAndSync() {
    const payload = await fetchJson("/api/panel/rebuild-fingerprint", { method: "POST" });
    await refreshStatus();
    try {
      await panelSyncCheck();
    } catch (_) {
      // Keep action usable even when admin sync check is not reachable.
    }
    if (payload && payload.synced) {
      toast("Fingerprint neu erstellt und an Admin gemeldet", "success");
    } else if (payload && payload.rebuilt) {
      toast("Fingerprint neu erstellt (Device aktuell nicht verlinkt)", "secondary");
    } else {
      toast("Fingerprint-Aktualisierung abgeschlossen", "success");
    }
  }

  async function toggleWifi() {
    const wifiEnabled = !!(((networkState || {}).interfaces || {}).wifi || {}).enabled;
    await fetchJson("/api/network/wifi/toggle", { method: "POST", body: { enabled: !wifiEnabled } });
    await refreshNetwork();
    toast("Wi-Fi updated", "success");
  }

  async function toggleBluetooth() {
    const btEnabled = !!(((networkState || {}).interfaces || {}).bluetooth || {}).enabled;
    await fetchJson("/api/network/bluetooth/toggle", { method: "POST", body: { enabled: !btEnabled } });
    await refreshNetwork();
    toast("Bluetooth aktualisiert", "success");
  }

  function renderBtPairingStatus(data) {
    const cleanBtText = (input) => String(input || "")
      .replace(/\x1B\[[0-9;]*[A-Za-z]/g, "")
      .replace(/\s+/g, " ")
      .trim();
    const looksLikeMenuHint = (text) => /return to menu main|type 'back'|^\s*back\s*$/i.test(text);

    btPairingLatest = data || {};
    const active = !!data.active;
    const badge = q("bt-pairing-status-badge");
    if (badge) {
      badge.classList.remove("text-bg-secondary", "text-bg-success", "text-bg-warning");
      badge.classList.add(active ? "text-bg-success" : "text-bg-secondary");
      badge.textContent = active ? "aktiv" : "inaktiv";
    }
    q("bt-pairing-remaining").textContent = active ? formatSeconds(data.remaining_seconds) : "-";
    const feedback = data.feedback || {};
    const passkey = cleanBtText(feedback.passkey || "");
    q("bt-pairing-passkey").textContent = passkey || "----";
    const deviceName = cleanBtText(feedback.device_name || "");
    const deviceMac = cleanBtText(feedback.device_mac || "");
    const deviceLabel = [deviceName, deviceMac].filter(Boolean).join(" ");
    q("bt-pairing-device").textContent = deviceLabel || "-";
    const rawMsg = cleanBtText(feedback.passkey_line || feedback.recent_line || "");
    q("bt-pairing-message").textContent = rawMsg && !looksLikeMenuHint(rawMsg) ? rawMsg : "Warte auf Pairing-Anfrage...";
  }

  function currentBtTargetMac() {
    const feedback = btPairingLatest.feedback || {};
    return String(feedback.pending_mac || feedback.device_mac || "").trim();
  }

  async function refreshBtPairingStatus() {
    const payload = await fetchJson("/api/network/bluetooth/pairing/status", { cache: "no-store" });
    const data = payload.data || {};
    renderBtPairingStatus(data);
    return data;
  }

  function startBtPairingPolling() {
    if (btPairingPollHandle) {
      window.clearInterval(btPairingPollHandle);
    }
    btPairingPollHandle = window.setInterval(async () => {
      try {
        await refreshBtPairingStatus();
      } catch (_) {
        // ignore transient polling errors
      }
    }, 900);
  }

  async function startBluetoothPairing() {
    const timeoutRaw = Number(q("bt-pairing-timeout-target")?.value || 180);
    if (!Number.isInteger(timeoutRaw) || timeoutRaw < 30 || timeoutRaw > 900) {
      throw new Error("Pairing-Timeout muss zwischen 30 und 900 Sekunden liegen.");
    }
    const modal = bootstrap.Modal.getOrCreateInstance(q("btPairingModal"));
    modal.show();
    q("bt-pairing-message").textContent = "Pairing wird gestartet...";
    startBtPairingPolling();
    try {
      await refreshBtPairingStatus();
    } catch (_) {
      // ignore initial status read errors; polling continues
    }

    try {
      await fetchJson("/api/network/bluetooth/pairing/start", {
        method: "POST",
        body: { timeout_seconds: timeoutRaw },
      });
      await refreshNetwork();
      toast("Bluetooth Pairing gestartet", "success");
    } catch (err) {
      let recovered = false;
      // Some environments/proxies cut long requests early although pairing already started.
      for (let attempt = 0; attempt < 8; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 1200));
        try {
          const state = await refreshBtPairingStatus();
          const feedback = (state && state.feedback) || {};
          if (state.active || feedback.passkey || feedback.pending_mac || feedback.device_mac) {
            recovered = true;
            break;
          }
        } catch (_) {
          // keep probing until retry budget is exhausted
        }
      }

      if (recovered) {
        q("bt-pairing-message").textContent = "Pairing läuft bereits. Warte auf Bestätigung...";
        toast("Pairing läuft bereits (Start-Request wurde zu früh beendet).", "secondary");
      } else {
        q("bt-pairing-message").textContent = `Start fehlgeschlagen: ${err && err.message ? err.message : String(err || "Unbekannter Fehler")}`;
        throw err;
      }
    }
    await refreshBtPairingStatus();
  }

  async function stopBluetoothPairing() {
    await fetchJson("/api/network/bluetooth/pairing/stop", { method: "POST" });
    await refreshNetwork();
    await refreshBtPairingStatus();
    toast("Bluetooth Pairing gestoppt", "success");
  }

  async function confirmBluetoothPairing() {
    const targetMac = currentBtTargetMac();
    if (!targetMac) {
      throw new Error("Keine Ziel-MAC für Bestätigung verfügbar.");
    }
    await fetchJson("/api/network/bluetooth/pairing/confirm", {
      method: "POST",
      body: { target_mac: targetMac },
      timeoutMs: 20000,
    });
    await refreshBtPairingStatus();
    await refreshNetwork();
    toast(`Kopplung bestätigt (${targetMac})`, "success");
  }

  async function rejectBluetoothPairing() {
    const targetMac = currentBtTargetMac();
    if (!targetMac) {
      throw new Error("Keine Ziel-MAC für Ablehnung verfügbar.");
    }
    await fetchJson("/api/network/bluetooth/pairing/reject", {
      method: "POST",
      body: { target_mac: targetMac },
      timeoutMs: 20000,
    });
    await refreshBtPairingStatus();
    await refreshNetwork();
    toast(`Kopplung abgelehnt (${targetMac})`, "secondary");
  }

  async function toggleLan() {
    const lan = (((networkState || {}).interfaces || {}).lan || {});
    await fetchJson("/api/network/lan/toggle", {
      method: "POST",
      body: { enabled: !lan.enabled, ifname: lan.ifname || "eth0" },
    });
    await refreshNetwork();
    toast("LAN updated", "success");
  }

  async function startWps(target = null) {
    const btn = q("btn-wps");
    const original = btn.innerHTML;
    if (target && target.ssid) {
      setWpsTarget(target);
    }
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>WPS startet...';
    toast("WPS wird gestartet...", "secondary");
    try {
      let triggerError = null;
      const payloadBody = {
        ifname: "wlan0",
      };
      if (selectedWpsTarget && selectedWpsTarget.ssid) {
        payloadBody.target_ssid = selectedWpsTarget.ssid;
      }
      if (selectedWpsTarget && selectedWpsTarget.bssid) {
        payloadBody.target_bssid = selectedWpsTarget.bssid;
      }
      try {
        const payload = await fetchJson("/api/network/wifi/wps/start", { method: "POST", body: payloadBody, timeoutMs: 20000 });
        const message = payload.message || "WPS wurde gestartet. Bitte jetzt innerhalb von 2 Minuten am Router die WPS-Taste druecken.";
        const hint = payload.hint || "Je nach Router kann die Verbindung 30-120 Sekunden dauern.";
        const net = ((payload.data || {}).network || {});
        const connectedInfo = net.ssid ? ` Verbunden mit SSID: ${net.ssid}.` : "";
        toast(`${message}${connectedInfo} ${hint}`, "success");
      } catch (err) {
        triggerError = err instanceof Error ? err : new Error(String(err));
        const msg = triggerError.message || "Unbekannter Fehler";
        const probablyStarted = /wps(_| )?pbc|WPS wurde gestartet/i.test(msg);
        if (probablyStarted) {
          toast(
            "WPS wurde ausgelöst. Verbindung wird weiter geprüft (30-120 Sekunden möglich).",
            "secondary",
          );
        } else {
          toast(
            `WPS-Trigger meldet Fehler, Verbindung wird trotzdem weiter geprüft: ${msg}`,
            "secondary",
          );
        }
      }

      await refreshNetwork();
      await refreshWpsStatus();
      await refreshWifiLogs();
      startWpsPolling();
      let connected = false;
      for (let i = 0; i < 10; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 6000));
        await refreshNetwork();
        const wifi = ((((networkState || {}).interfaces || {}).wifi) || {});
        if (wifi.connected) {
          toast(`WLAN verbunden: ${wifi.ssid || "SSID unbekannt"}`, "success");
          connected = true;
          break;
        }
      }

      if (!connected && triggerError) {
        toast(
          "WPS wurde offenbar nicht sauber gestartet oder Verbindung blieb aus. Bitte WPS am Router erneut drücken und nochmal versuchen.",
          "danger",
        );
      } else if (!connected) {
        toast("WPS gestartet, aber noch keine WLAN-Verbindung erkannt.", "secondary");
      }
    } finally {
      btn.disabled = false;
      btn.innerHTML = original;
    }
  }

  async function fixTailscaleDns() {
    const btn = q("btn-fix-tailscale-dns");
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Fix läuft...';
    try {
      const payload = await fetchJson("/api/system/tailscale/disable-dns", { method: "POST", timeoutMs: 30000 });
      const data = payload.data || {};
      q("sys-dnsfix-connection").textContent = data.connection || "-";
      q("sys-dnsfix-dns").textContent = data.dns || "-";
      q("sys-dnsfix-search").textContent = data.search || "-";
      await refreshNetwork();
      toast(data.message || "Tailscale DNS-Override deaktiviert.", "success");
    } finally {
      btn.disabled = false;
      btn.innerHTML = original;
    }
  }

  async function saveStorageSecuritySettings() {
    const enabled = !!q("system-storage-delete-hardcore").checked;
    const payload = await fetchJson("/api/system/settings", {
      method: "POST",
      body: {
        storage_delete_hardcore_mode: enabled,
      },
      timeoutMs: 12000,
    });
    portalSecurityState.storageDeleteHardcoreMode = !!(payload.data || {}).storage_delete_hardcore_mode;
    const statusBadge = q("system-storage-security-status");
    if (statusBadge) {
      statusBadge.classList.remove("text-bg-success", "text-bg-secondary");
      if (portalSecurityState.storageDeleteHardcoreMode) {
        statusBadge.classList.add("text-bg-success");
        statusBadge.textContent = "aktiv";
      } else {
        statusBadge.classList.add("text-bg-secondary");
        statusBadge.textContent = "inaktiv";
      }
    }
    toast("Storage-Sicherheitseinstellung gespeichert.", "success");
  }

  function renderSentinelsUi() {
    const state = portalSecurityState.sentinels || {};
    const items = Array.isArray(state.items) ? state.items : [];
    const sourceDirEl = q("sentinel-source-dir");
    const sourceStatusEl = q("sentinel-source-status");
    const configPathEl = q("sentinel-config-path");
    const listEl = q("sentinel-list");
    const countBadge = q("sentinel-count-badge");
    const webhookInput = q("sentinel-webhook-url");
    if (webhookInput && document.activeElement !== webhookInput) {
      webhookInput.value = String(state.webhookUrl || "");
    }
    if (sourceDirEl) {
      sourceDirEl.textContent = state.sourceDir || "-";
    }
    if (configPathEl) {
      configPathEl.textContent = state.configPath || "-";
    }
    if (sourceStatusEl) {
      sourceStatusEl.textContent = state.sourceError ? `Fehler: ${state.sourceError}` : "bereit";
    }
    if (countBadge) {
      countBadge.textContent = `${items.length} Module`;
      countBadge.classList.remove("text-bg-secondary", "text-bg-success");
      countBadge.classList.add(items.length ? "text-bg-success" : "text-bg-secondary");
    }
    if (!listEl) return;
    if (!items.length) {
      listEl.innerHTML = '<div class="text-secondary small">Keine Sentinel-Module gefunden.</div>';
      return;
    }

    listEl.innerHTML = "";
    for (const item of items) {
      const row = document.createElement("div");
      row.className = "list-group-item d-flex justify-content-between align-items-start gap-2";

      const left = document.createElement("div");
      const title = document.createElement("div");
      title.className = "fw-semibold";
      title.textContent = String(item.name || item.slug || "-");
      const desc = document.createElement("div");
      desc.className = "small text-secondary";
      desc.textContent = String(item.description || "");
      const meta = document.createElement("div");
      meta.className = "small mt-1";
      const parts = [];
      parts.push(`mode: ${item.install_mode || "-"}`);
      parts.push(`state: ${item.state || "-"}`);
      if (item.service_name) parts.push(`service: ${item.service_name}`);
      if (item.timer_name) parts.push(`timer: ${item.timer_name}`);
      meta.textContent = parts.join(" | ");
      left.append(title, desc, meta);

      const right = document.createElement("div");
      right.className = "d-flex flex-column gap-1 align-items-end";

      const badge = document.createElement("span");
      badge.className = "badge";
      if (item.installed) {
        badge.classList.add(item.active ? "text-bg-success" : "text-bg-warning", "text-dark");
        badge.textContent = item.active ? "aktiv" : "installiert";
      } else {
        badge.classList.add("text-bg-secondary");
        badge.textContent = "nicht installiert";
      }

      const installBtn = document.createElement("button");
      installBtn.type = "button";
      installBtn.className = "btn btn-outline-success btn-sm";
      installBtn.textContent = "Installieren";
      installBtn.dataset.action = "install";
      installBtn.dataset.slug = String(item.slug || "");
      installBtn.disabled = !!item.installed;

      const uninstallBtn = document.createElement("button");
      uninstallBtn.type = "button";
      uninstallBtn.className = "btn btn-outline-danger btn-sm";
      uninstallBtn.textContent = "Entfernen";
      uninstallBtn.dataset.action = "uninstall";
      uninstallBtn.dataset.slug = String(item.slug || "");
      uninstallBtn.disabled = !item.installed;

      right.append(badge, installBtn, uninstallBtn);
      row.append(left, right);
      listEl.appendChild(row);
    }
  }

  function applySentinelPayload(data) {
    const payload = data || {};
    portalSecurityState.sentinels.webhookUrl = String(payload.webhook_url || "");
    portalSecurityState.sentinels.sourceDir = String(payload.source_dir || "");
    portalSecurityState.sentinels.sourceError = String(payload.source_error || "");
    portalSecurityState.sentinels.configPath = String(payload.config_path || "");
    portalSecurityState.sentinels.items = Array.isArray(payload.sentinels) ? payload.sentinels : [];
    renderSentinelsUi();
  }

  async function refreshSentinelStatus() {
    const payload = await fetchJson("/api/network/security/sentinels/status", { cache: "no-store", timeoutMs: 12000 });
    applySentinelPayload(payload.data || {});
    toast("Sentinel-Status aktualisiert.", "success");
  }

  async function saveSentinelWebhook() {
    const webhookUrl = String(q("sentinel-webhook-url")?.value || "").trim();
    if (!webhookUrl) {
      throw new Error("Bitte zuerst eine Webhook URL eintragen.");
    }
    const payload = await fetchJson("/api/network/security/sentinels/webhook", {
      method: "POST",
      body: { webhook_url: webhookUrl },
      timeoutMs: 12000,
    });
    applySentinelPayload(payload.data || {});
    toast(payload.message || "Webhook URL gespeichert.", "success");
  }

  async function installSentinel(slug) {
    const payload = await fetchJson("/api/network/security/sentinels/install", {
      method: "POST",
      body: { slug },
      timeoutMs: 60000,
    });
    applySentinelPayload(payload.data || {});
    toast(payload.message || `Sentinel ${slug} installiert.`, "success");
  }

  async function uninstallSentinel(slug) {
    const payload = await fetchJson("/api/network/security/sentinels/uninstall", {
      method: "POST",
      body: { slug },
      timeoutMs: 60000,
    });
    applySentinelPayload(payload.data || {});
    toast(payload.message || `Sentinel ${slug} entfernt.`, "success");
  }

  function renderNetworkSecurityList(hostId, rows, kind) {
    const host = q(hostId);
    if (!host) return;
    const items = Array.isArray(rows) ? rows : [];
    if (!items.length) {
      host.innerHTML = '<div class="text-secondary">Keine Einträge.</div>';
      return;
    }
    host.innerHTML = "";
    for (const item of items) {
      const row = document.createElement("div");
      row.className = "list-group-item d-flex justify-content-between align-items-center gap-2";
      const title = document.createElement("span");
      title.className = "text-truncate";
      const label = String(item.label || item.name || item.ssid || item.connection || item.mac || item.key || "-").trim();
      title.textContent = label || "-";
      row.appendChild(title);

      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.className = "btn btn-outline-danger btn-sm";
      removeBtn.textContent = "Entfernen";
      removeBtn.dataset.kind = kind;
      removeBtn.dataset.key = String(item.key || "").trim();
      row.appendChild(removeBtn);
      host.appendChild(row);
    }
  }

  function renderNetworkKnownList(hostId, rows) {
    const host = q(hostId);
    if (!host) return;
    const items = Array.isArray(rows) ? rows : [];
    if (!items.length) {
      host.innerHTML = '<div class="text-secondary">Keine Daten.</div>';
      return;
    }
    host.innerHTML = "";
    for (const item of items) {
      const row = document.createElement("div");
      row.className = "list-group-item";
      row.textContent = String(item || "-").trim() || "-";
      host.appendChild(row);
    }
  }

  function renderNetworkSecurityUi() {
    const profile = portalSecurityState.networkSecurity || {};
    const assessment = profile.assessment || null;
    const enabled = !!profile.enabled;

    const badge = q("security-perimeter-badge");
    if (badge) {
      badge.classList.remove("text-bg-secondary", "text-bg-success", "text-bg-danger", "text-bg-warning");
      if (!enabled) {
        badge.classList.add("text-bg-secondary");
        badge.textContent = "inaktiv";
      } else if (assessment && assessment.in_perimeter) {
        badge.classList.add("text-bg-success");
        badge.textContent = "sicher";
      } else if (assessment) {
        badge.classList.add("text-bg-danger");
        badge.textContent = "außerhalb";
      } else {
        badge.classList.add("text-bg-warning");
        badge.textContent = "prüfe...";
      }
    }

    const stateEl = q("security-perimeter-state");
    if (stateEl) {
      if (!enabled) {
        stateEl.textContent = "Status: Eingrenzung aus.";
      } else if (!assessment) {
        stateEl.textContent = "Status: Bewertung läuft...";
      } else if (assessment.in_perimeter) {
        stateEl.textContent = "Status: Innerhalb der als sicher markierten Umgebung.";
      } else {
        stateEl.textContent = "Status: Aktuelle Verbindungen passen zu keinem sicheren Marker.";
      }
    }

    renderNetworkSecurityList("security-trusted-wifi", profile.trusted_wifi || [], "wifi");
    renderNetworkSecurityList("security-trusted-lan", profile.trusted_lan || [], "lan");
    renderNetworkSecurityList("security-trusted-bt", profile.trusted_bluetooth || [], "bluetooth");

    const catalog = profile.catalog || {};
    const knownWifi = [];
    const knownLan = [];
    const knownBt = [];
    for (const item of (Array.isArray(catalog.known_wifi) ? catalog.known_wifi : [])) {
      knownWifi.push([item.ssid || "-", item.source || "-"].join(" | "));
    }
    for (const item of (Array.isArray(catalog.known_lan) ? catalog.known_lan : [])) {
      knownLan.push([item.ifname || "-", item.connection || "-", item.gateway_ip || "-", item.gateway_mac || "-"].join(" | "));
    }
    for (const item of (Array.isArray(catalog.known_bluetooth) ? catalog.known_bluetooth : [])) {
      knownBt.push([item.name || "-", item.mac || "-"].join(" | "));
    }
    renderNetworkKnownList("security-known-wifi", knownWifi);
    renderNetworkKnownList("security-known-lan", knownLan);
    renderNetworkKnownList("security-known-bt", knownBt);
  }

  async function refreshNetworkSecurityLive() {
    const payload = await fetchJson("/api/network/security/status", { cache: "no-store", timeoutMs: 10000 });
    const data = (payload && payload.data) || {};
    const profile = data.profile || {};
    const assessment = data.assessment || null;
    portalSecurityState.networkSecurity.enabled = !!profile.enabled;
    portalSecurityState.networkSecurity.trusted_wifi = Array.isArray(profile.trusted_wifi) ? profile.trusted_wifi : [];
    portalSecurityState.networkSecurity.trusted_lan = Array.isArray(profile.trusted_lan) ? profile.trusted_lan : [];
    portalSecurityState.networkSecurity.trusted_bluetooth = Array.isArray(profile.trusted_bluetooth) ? profile.trusted_bluetooth : [];
    portalSecurityState.networkSecurity.assessment = assessment;
    portalSecurityState.networkSecurity.catalog = data.catalog || null;
    const perimeterToggle = q("security-perimeter-enabled");
    if (perimeterToggle) {
      perimeterToggle.checked = portalSecurityState.networkSecurity.enabled;
    }
    renderNetworkSecurityUi();
    toast("Security Live-Daten aktualisiert.", "success");
  }

  async function saveNetworkSecuritySettings() {
    const enabled = !!q("security-perimeter-enabled")?.checked;
    const payload = await fetchJson("/api/network/security/settings", {
      method: "POST",
      body: { enabled },
      timeoutMs: 12000,
    });
    const profile = (payload.data || {}).profile || {};
    portalSecurityState.networkSecurity.enabled = !!profile.enabled;
    portalSecurityState.networkSecurity.trusted_wifi = Array.isArray(profile.trusted_wifi) ? profile.trusted_wifi : [];
    portalSecurityState.networkSecurity.trusted_lan = Array.isArray(profile.trusted_lan) ? profile.trusted_lan : [];
    portalSecurityState.networkSecurity.trusted_bluetooth = Array.isArray(profile.trusted_bluetooth) ? profile.trusted_bluetooth : [];
    await refreshNetwork();
    renderNetworkSecurityUi();
    toast("Standort-Härtung gespeichert.", "success");
  }

  async function trustCurrentNetwork(kind) {
    const body = {
      wifi: kind === "wifi",
      lan: kind === "lan",
      bluetooth: kind === "bluetooth",
    };
    const payload = await fetchJson("/api/network/security/trust/current", {
      method: "POST",
      body,
      timeoutMs: 12000,
    });
    const profile = (payload.data || {}).profile || {};
    const assessment = (payload.data || {}).assessment || null;
    portalSecurityState.networkSecurity.enabled = !!profile.enabled;
    portalSecurityState.networkSecurity.trusted_wifi = Array.isArray(profile.trusted_wifi) ? profile.trusted_wifi : [];
    portalSecurityState.networkSecurity.trusted_lan = Array.isArray(profile.trusted_lan) ? profile.trusted_lan : [];
    portalSecurityState.networkSecurity.trusted_bluetooth = Array.isArray(profile.trusted_bluetooth) ? profile.trusted_bluetooth : [];
    portalSecurityState.networkSecurity.assessment = assessment;
    renderNetworkSecurityUi();
    await refreshNetwork();
    toast("Sicherheits-Marker übernommen.", "success");
  }

  async function removeTrustedNetworkMarker(kind, key) {
    await fetchJson("/api/network/security/trust/remove", {
      method: "POST",
      body: { kind, key },
      timeoutMs: 12000,
    });
    await refreshNetwork();
    toast("Sicherheits-Marker entfernt.", "secondary");
  }

  function setHostnameRenameError(message = "") {
    const alertEl = q("hostname-rename-alert");
    if (!alertEl) return;
    const text = String(message || "").trim();
    if (!text) {
      alertEl.classList.add("d-none");
      alertEl.textContent = "";
      return;
    }
    alertEl.textContent = text;
    alertEl.classList.remove("d-none");
  }

  function renderHostnameCurrentInfo() {
    const status = statusDashboardState.status || {};
    const state = status.state || {};
    const net = networkState || {};
    const wifi = ((net.interfaces || {}).wifi || {});
    const lan = ((net.interfaces || {}).lan || {});
    const bt = ((net.interfaces || {}).bluetooth || {});
    const apActive = String(q("ap-active-badge")?.textContent || "-").trim();
    const apSsid = String(q("ap-ssid")?.textContent || "-").trim();

    q("hostname-rename-current-host").textContent = state.hostname || net.hostname || "-";
    q("hostname-rename-current-lan").textContent = `${lan.ifname || "eth0"} | ${lan.ip || "-"} | carrier: ${lan.carrier ? "yes" : "no"}`;
    q("hostname-rename-current-wifi").textContent = `${wifi.ifname || "wlan0"} | ${wifi.ssid || "-"} | ${wifi.ip || "-"} | connected: ${wifi.connected ? "yes" : "no"}`;
    q("hostname-rename-current-bt").textContent = bt.enabled ? "enabled" : "disabled";
    q("hostname-rename-current-ap").textContent = `${apSsid || "-"} | ${apActive || "-"}`;
  }

  function renderHostnamePreview(preview) {
    const data = preview || {};
    const nextHost = String(data.next_hostname || "").trim();
    const derived = data.derived || {};
    q("hostname-rename-preview-host").textContent = nextHost || "-";
    q("hostname-rename-preview-ap").textContent = derived.ap_ssid || "-";
    q("hostname-rename-preview-bt").textContent = derived.bt_name || "-";
  }

  function updateHostnameRenameSaveButton() {
    const btn = q("btn-hostname-rename-save");
    if (!btn) return;
    const hardcore = !!q("hostname-rename-hardcore")?.checked;
    const confirmText = String(q("hostname-rename-confirm")?.value || "").trim();
    const hasPreview = !!(hostnameRenameState.preview && hostnameRenameState.preview.next_hostname);
    btn.disabled = !(hasPreview && (!hardcore || confirmText === "Hostname Ändern"));
  }

  async function refreshHostnameRenamePreview() {
    const inputEl = q("hostname-rename-input");
    const hostname = String(inputEl?.value || "").trim();
    setHostnameRenameError("");
    if (!hostname) {
      hostnameRenameState.preview = null;
      renderHostnamePreview(null);
      updateHostnameRenameSaveButton();
      return;
    }
    try {
      const payload = await fetchJson("/api/system/hostname/preview", {
        method: "POST",
        body: {
          hostname,
          ap_profile: "jm-hotspot",
        },
        timeoutMs: 10000,
      });
      const preview = payload.data || {};
      hostnameRenameState.preview = preview;
      renderHostnamePreview(preview);
      updateHostnameRenameSaveButton();
    } catch (err) {
      hostnameRenameState.preview = null;
      renderHostnamePreview(null);
      setHostnameRenameError(err && err.message ? err.message : String(err || "Preview fehlgeschlagen"));
      updateHostnameRenameSaveButton();
    }
  }

  function openHostnameRenameModal() {
    const status = statusDashboardState.status || {};
    const state = status.state || {};
    const currentHost = String(state.hostname || (networkState || {}).hostname || "").trim();
    q("hostname-rename-input").value = currentHost || "";
    q("hostname-rename-confirm").value = "";
    q("hostname-rename-hardcore").checked = true;
    hostnameRenameState.preview = null;
    renderHostnameCurrentInfo();
    setHostnameRenameError("");
    updateHostnameRenameSaveButton();
    run(refreshHostnameRenamePreview);
  }

  async function saveHostnameRename() {
    const preview = hostnameRenameState.preview || {};
    const nextHost = String(preview.next_hostname || "").trim();
    if (!nextHost) {
      throw new Error("Bitte zuerst gültigen Hostname prüfen.");
    }
    const hardcore = !!q("hostname-rename-hardcore")?.checked;
    const confirmText = String(q("hostname-rename-confirm")?.value || "").trim();
    if (hardcore && confirmText !== "Hostname Ändern") {
      throw new Error('Bitte exakt "Hostname Ändern" eingeben.');
    }

    const payload = await fetchJson("/api/system/hostname/rename", {
      method: "POST",
      body: {
        hostname: nextHost,
        confirm_phrase: hardcore ? confirmText : "Hostname Ändern",
        ap_profile: "jm-hotspot",
      },
      timeoutMs: 30000,
    });

    const modalEl = q("hostnameRenameModal");
    const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    modal.hide();

    await refreshStatus();
    await refreshNetwork();
    await refreshApStatus();
    try {
      await panelSyncNow();
    } catch (syncErr) {
      toast(`Hostname geändert, Admin-Sync fehlgeschlagen: ${syncErr.message || syncErr}`, "warning");
    }
    const updated = ((payload.data || {}).new_hostname || nextHost);
    toast(`Hostname aktualisiert: ${updated}`, "success");
  }

  async function requestSystemPower(action) {
    const target = String(action || "").toLowerCase();
    if (target !== "shutdown" && target !== "reboot") {
      throw new Error("Ungültige Systemaktion.");
    }
    const label = target === "shutdown" ? "Ausschalten" : "Neustarten";
    const confirmed = window.confirm(`Raspberry Pi wirklich ${label}?`);
    if (!confirmed) return;
    await fetchJson("/api/system/power", {
      method: "POST",
      body: { action: target },
      timeoutMs: 12000,
    });
    toast(target === "shutdown" ? "Ausschalten wurde angefordert." : "Neustart wurde angefordert.", "success");
  }

  async function restartPortalServiceNow() {
    const confirmed = window.confirm("Portal-Service jetzt neu starten?");
    if (!confirmed) return;
    try {
      await fetchJson("/api/system/portal/restart", {
        method: "POST",
        timeoutMs: 12000,
      });
      toast("Portal-Neustart angefordert. Seite wird neu verbunden …", "success");
    } catch (err) {
      const detail = String(err && err.message ? err.message : err || "").toLowerCase();
      const expectedRestartDrop = detail.includes("502") || detail.includes("bad gateway") || detail.includes("invalid_json");
      if (!expectedRestartDrop) {
        throw err;
      }
      toast("Portal startet neu. Kurz warten, dann wird die Seite neu verbunden …", "warning");
    }
    window.setTimeout(() => {
      window.location.reload();
    }, 4500);
  }

  async function updatePortal() {
    const btn = q("btn-system-update-portal");
    const logEl = q("system-update-log");
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Update läuft...';
    try {
      const payload = await fetchJson("/api/system/portal/update", { method: "POST", timeoutMs: 30000 });
      const data = payload.data || {};
      currentUpdateJobId = String(data.job_id || "").trim();
      if (currentUpdateJobId) {
        await pollPortalUpdateStatus(currentUpdateJobId, true);
      } else {
        logEl.textContent = "Update wurde gestartet, aber keine Job-ID wurde zurückgegeben.";
      }
      toast(data.message || "Update ausgelöst. Service wird neu gestartet.", "success");
    } finally {
      btn.disabled = false;
      btn.innerHTML = original;
    }
  }

  function renderPortalUpdateStatus(data) {
    const logEl = q("system-update-log");
    const status = String(data.status || "unknown");
    const success = !!data.success;
    const lines = [
      `status: ${status}`,
      `success: ${String(success)}`,
      `message: ${data.message || "-"}`,
      `job_id: ${data.job_id || "-"}`,
      `repo: ${data.repo_dir || "-"}`,
      `user: ${data.service_user || "-"}`,
      `service: ${data.service_name || "-"}`,
      `git_status: ${data.git_status || "-"}`,
      `commit: ${(data.before_commit || "-")} -> ${(data.after_commit || "-")}`,
      `player_update_triggered: ${String(!!data.player_update_triggered)}`,
      `player_update_job_id: ${data.player_update_job_id || "-"}`,
      `player_update_reason: ${data.player_update_reason || "-"}`,
      `player_update_needed: ${data.player_update_needed || "-"}`,
      `started_at: ${data.started_at || "-"}`,
      `finished_at: ${data.finished_at || "-"}`,
      "",
      "log:",
      data.log || "-",
    ];
    logEl.textContent = lines.join("\n");
    logEl.scrollTop = logEl.scrollHeight;
    try {
      window.localStorage.setItem(UPDATE_CACHE_KEY, JSON.stringify({ data, cached_at: new Date().toISOString() }));
    } catch (_) {
      // ignore cache errors
    }
  }

  async function fetchPortalUpdateStatus(jobId = "") {
    const query = jobId ? `?job_id=${encodeURIComponent(jobId)}` : "";
    const payload = await fetchJson(`/api/system/portal/update/status${query}`, { timeoutMs: 8000, cache: "no-store" });
    const data = payload.data || {};
    renderPortalUpdateStatus(data);
    return data;
  }

  function persistUpdateResultFlash(message, type) {
    try {
      window.sessionStorage.setItem(
        UPDATE_RESULT_FLASH_KEY,
        JSON.stringify({
          message: String(message || ""),
          type: String(type || "secondary"),
          ts: Date.now(),
        }),
      );
    } catch (_) {
      // ignore storage errors
    }
  }

  function flushPersistedUpdateResultFlash() {
    try {
      const raw = window.sessionStorage.getItem(UPDATE_RESULT_FLASH_KEY);
      if (!raw) return;
      window.sessionStorage.removeItem(UPDATE_RESULT_FLASH_KEY);
      const parsed = JSON.parse(raw);
      const message = String(parsed?.message || "").trim();
      const type = String(parsed?.type || "secondary");
      if (message) {
        toast(message, type);
      }
    } catch (_) {
      // ignore broken cache
    }
  }

  async function trySyncUpdateResultToAdmin() {
    try {
      const state = (statusDashboardState.status || {}).state || {};
      const panel = state.panel || {};
      const linked = !!panel.linked;
      const adminBase = String(els.adminBase?.value || "").trim();
      if (!linked || !adminBase) {
        return false;
      }
      await panelSyncNow();
      await refreshStatus();
      return true;
    } catch (syncErr) {
      const detail = syncErr && syncErr.message ? syncErr.message : String(syncErr || "");
      toast(`Portal-Update ok, Admin-Sync fehlgeschlagen: ${detail || "unbekannter Fehler"}`, "warning");
      return false;
    }
  }

  async function pollPortalUpdateStatus(jobId, announceDone = false) {
    if (updatePollHandle) {
      clearInterval(updatePollHandle);
      updatePollHandle = null;
    }
    let networkErrors = 0;
    const tick = async () => {
      try {
        const data = await fetchPortalUpdateStatus(jobId);
        networkErrors = 0;
        const status = String(data.status || "").toLowerCase();
        if (status === "done" || status === "failed") {
          if (updatePollHandle) {
            clearInterval(updatePollHandle);
            updatePollHandle = null;
          }
          if (announceDone) {
            const done = status === "done";
            toast(
              done ? "Portal-Update abgeschlossen." : "Portal-Update fehlgeschlagen.",
              done ? "success" : "danger",
            );
            if (done) {
              // Force status re-check so Hero update badge reflects the new revision.
              await refreshStatus();
              await trySyncUpdateResultToAdmin();
              await new Promise((resolve) => window.setTimeout(resolve, 1200));
              await refreshStatus();
              persistUpdateResultFlash("Portal-Update erfolgreich abgeschlossen. Seite wurde neu geladen.", "success");
              window.setTimeout(() => {
                window.location.reload();
              }, 2200);
            } else {
              persistUpdateResultFlash("Portal-Update fehlgeschlagen. Details im Update-Tab prüfen.", "danger");
            }
          }
          await run(refreshStatus);
        }
      } catch (err) {
        networkErrors += 1;
        if (networkErrors >= 20) {
          // Fallback: service might have restarted; check latest status without job id.
          try {
            const latest = await fetchPortalUpdateStatus("");
            const latestStatus = String(latest.status || "").toLowerCase();
            if (latestStatus === "done" || latestStatus === "failed") {
              if (updatePollHandle) {
                clearInterval(updatePollHandle);
                updatePollHandle = null;
              }
              if (announceDone) {
                const done = latestStatus === "done";
                toast(done ? "Portal-Update abgeschlossen." : "Portal-Update fehlgeschlagen.", done ? "success" : "danger");
                if (done) {
                  await refreshStatus();
                  await trySyncUpdateResultToAdmin();
                  persistUpdateResultFlash("Portal-Update erfolgreich abgeschlossen.", "success");
                  window.setTimeout(() => window.location.reload(), 2200);
                }
              }
              return;
            }
          } catch (_) {
            // continue with original error below
          }
          if (updatePollHandle) {
            clearInterval(updatePollHandle);
            updatePollHandle = null;
          }
          throw err;
        }
      }
    };
    await tick();
    updatePollHandle = setInterval(() => {
      run(tick);
    }, 2000);
  }

  async function loadLastPortalUpdateStatus() {
    try {
      const raw = window.localStorage.getItem(UPDATE_CACHE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed && parsed.data && typeof parsed.data === "object") {
          renderPortalUpdateStatus(parsed.data);
        }
      }
    } catch (_) {
      // ignore cache read errors
    }

    try {
      const data = await fetchPortalUpdateStatus("");
      const status = String(data.status || "").toLowerCase();
      const hasJob = !!String(data.job_id || "").trim();
      if (hasJob && (status === "running" || status === "restarting")) {
        currentUpdateJobId = String(data.job_id || "").trim();
        await pollPortalUpdateStatus(currentUpdateJobId, false);
      }
    } catch (_) {
      // Keep UI usable even if status endpoint is temporarily unavailable.
    }
  }

  function openUpdateTab() {
    const systemTab = q("system-tab");
    const systemSubUpdateTab = q("system-sub-update-tab");
    const updatePane = q("system-sub-update");
    if (systemTab && window.bootstrap && window.bootstrap.Tab) {
      window.bootstrap.Tab.getOrCreateInstance(systemTab).show();
    } else if (systemTab) {
      systemTab.click();
    }
    if (systemSubUpdateTab && window.bootstrap && window.bootstrap.Tab) {
      window.bootstrap.Tab.getOrCreateInstance(systemSubUpdateTab).show();
    } else if (systemSubUpdateTab) {
      systemSubUpdateTab.click();
    }
    if (updatePane && typeof updatePane.scrollIntoView === "function") {
      updatePane.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  function bindButtons() {
    q("status-mode-badge").addEventListener("click", () => {
      const status = statusDashboardState.status || {};
      const state = status.state || {};
      const cfg = status.config || {};
      const panel = state.panel || cfg.panel_link_state || {};
      if (!panel.linked) {
        openSetupWizard();
      }
    });
    q("btn-link-refresh-status").addEventListener("click", () => run(async () => {
      await refreshState();
      await refreshPanelFlagsLive();
    }));
    q("btn-display-refresh").addEventListener("click", () => run(refreshStatus));
    q("btn-link-register").addEventListener("click", () => run(panelRegister));
    q("btn-link-assign").addEventListener("click", () => openSetupWizard("assign"));
    q("btn-link-rebuild-fingerprint").addEventListener("click", () => run(rebuildFingerprintAndSync));
    const btnPullPlan = q("btn-pull-plan");
    if (btnPullPlan) {
      btnPullPlan.addEventListener("click", () => run(pullPlan));
    }
    q("btn-stream-refresh").addEventListener("click", () => run(refreshStreamOverview));
    q("btn-stream-select-save").addEventListener("click", () => run(saveSelectedStream));
    q("btn-stream-sync").addEventListener("click", () => run(syncSelectedStream));
    q("btn-player-status-refresh").addEventListener("click", () => run(refreshPlayerStatus));
    q("btn-player-start").addEventListener("click", () => run(() => playerAction("start")));
    q("btn-player-stop").addEventListener("click", () => run(() => playerAction("stop")));
    q("btn-player-restart").addEventListener("click", () => run(() => playerAction("restart")));
    q("btn-stream-audio-refresh").addEventListener("click", () => run(async () => {
      await refreshStreamAudioFiles();
      await refreshStreamAudioStatus();
    }));
    q("btn-stream-audio-play-file").addEventListener("click", () => run(streamAudioPlayFile));
    q("btn-stream-audio-play-stream").addEventListener("click", () => run(streamAudioPlayStream));
    q("btn-stream-audio-pause").addEventListener("click", () => run(() => streamAudioAction("pause")));
    q("btn-stream-audio-resume").addEventListener("click", () => run(() => streamAudioAction("resume")));
    q("btn-stream-audio-stop").addEventListener("click", () => run(() => streamAudioAction("stop")));
    q("btn-stream-audio-mute").addEventListener("click", () => run(toggleStreamAudioMute));
    q("stream-audio-volume").addEventListener("input", () => {
      const raw = String(q("stream-audio-volume")?.value || "").trim();
      const vol = Number(raw);
      if (!Number.isFinite(vol)) return;
      const clamped = Math.max(0, Math.min(100, Math.round(vol)));
      const valueEl = q("stream-audio-volume-value");
      if (valueEl) valueEl.textContent = String(clamped);
      scheduleStreamAudioVolumeSet();
    });
    q("stream-audio-volume").addEventListener("change", () => run(() => streamAudioSetVolume({ notify: false, refresh: true })));
    q("stream-audio-file-select").addEventListener("change", () => {
      const selected = String(q("stream-audio-file-select")?.value || "").trim();
      if (selected) q("stream-audio-file-path").value = selected;
    });
    q("btn-stream-player-repo-save").addEventListener("click", () => run(savePlayerRepoConfig));
    q("btn-stream-player-install-update").addEventListener("click", () => run(startStreamPlayerInstallUpdate));
    q("btn-stream-player-update-status").addEventListener("click", () => run(() => pollStreamPlayerUpdateStatus(streamPlayerUpdateJobId)));
    q("btn-panel-sync-check").addEventListener("click", () => run(panelSyncCheck));
    q("btn-panel-sync-now").addEventListener("click", () => run(panelSyncNow));
    const syncRefreshBtn = q("btn-sync-refresh-config");
    if (syncRefreshBtn) {
      syncRefreshBtn.addEventListener("click", () => run(pullSyncConfig));
    }
    const syncRunBtn = q("btn-sync-run-now");
    if (syncRunBtn) {
      syncRunBtn.addEventListener("click", () => run(runPortalSyncNow));
    }

    els.btnWifiToggle.addEventListener("click", () => run(toggleWifi));
    els.btnBtToggle.addEventListener("click", () => run(toggleBluetooth));
    els.btnBtPairingStart.addEventListener("click", () => run(startBluetoothPairing));
    q("btn-bt-pairing-confirm").addEventListener("click", () => run(confirmBluetoothPairing));
    q("btn-bt-pairing-reject").addEventListener("click", () => run(rejectBluetoothPairing));
    q("btn-bt-pairing-stop").addEventListener("click", () => run(stopBluetoothPairing));
    els.btnLanToggle.addEventListener("click", () => run(toggleLan));
    q("btn-wps").addEventListener("click", () => run(startWps));
    q("btn-refresh-network").addEventListener("click", () => run(refreshNetwork));
    q("btn-wifi-scan").addEventListener("click", () => run(refreshWifiScan));
    q("btn-wifi-profiles-refresh").addEventListener("click", () => run(refreshWifiProfiles));
    q("btn-wifi-profiles-apply").addEventListener("click", () => run(wifiProfilesApply));
    q("btn-wifi-manual-add").addEventListener("click", () => run(wifiProfilesAddManual));
    q("btn-wifi-logs-refresh").addEventListener("click", () => run(refreshWifiLogs));
    q("btn-storage-refresh").addEventListener("click", () => run(refreshStorageStatus));
    q("btn-storage-fm-back").addEventListener("click", () => closeStorageFileManager());
    q("btn-storage-fm-dir-up").addEventListener("click", () => run(storageFileManagerGoUp));
    q("btn-storage-fm-new-folder").addEventListener("click", () => run(storageFileManagerCreateFolder));
    q("btn-storage-fm-rename").addEventListener("click", () => run(storageFileManagerRenameSelected));
    q("btn-storage-new-folder-confirm").addEventListener("click", () => run(storageFileManagerCreateFolderConfirmed));
    q("btn-storage-rename-confirm").addEventListener("click", () => run(storageFileManagerRenameConfirmed));
    q("btn-storage-fm-select-all").addEventListener("click", () => storageFileManagerSelectAll());
    q("btn-storage-fm-unselect-all").addEventListener("click", () => storageFileManagerUnselectAll());
    q("btn-storage-fm-delete-selected").addEventListener("click", () => run(storageFileManagerDeleteSelected));
    q("btn-system-storage-security-save").addEventListener("click", () => run(saveStorageSecuritySettings));
    q("btn-security-refresh-live").addEventListener("click", () => run(refreshNetworkSecurityLive));
    q("btn-security-save").addEventListener("click", () => run(saveNetworkSecuritySettings));
    q("btn-security-trust-wifi").addEventListener("click", () => run(() => trustCurrentNetwork("wifi")));
    q("btn-security-trust-lan").addEventListener("click", () => run(() => trustCurrentNetwork("lan")));
    q("btn-security-trust-bt").addEventListener("click", () => run(() => trustCurrentNetwork("bluetooth")));
    q("btn-security-ap-disable").addEventListener("click", () => run(() => toggleAp(false)));
    q("btn-sentinel-refresh").addEventListener("click", () => run(refreshSentinelStatus));
    q("btn-sentinel-webhook-save").addEventListener("click", () => run(saveSentinelWebhook));
    q("btn-hostname-rename-save").addEventListener("click", () => run(saveHostnameRename));
    q("hostname-rename-input").addEventListener("input", () => {
      if (hostnameRenameState.previewTimer) {
        window.clearTimeout(hostnameRenameState.previewTimer);
      }
      hostnameRenameState.previewTimer = window.setTimeout(() => {
        run(refreshHostnameRenamePreview);
      }, 250);
      updateHostnameRenameSaveButton();
    });
    q("hostname-rename-hardcore").addEventListener("change", updateHostnameRenameSaveButton);
    q("hostname-rename-confirm").addEventListener("input", updateHostnameRenameSaveButton);
    q("hostnameRenameModal").addEventListener("shown.bs.modal", () => {
      openHostnameRenameModal();
    });
    q("hostnameRenameModal").addEventListener("hidden.bs.modal", () => {
      setHostnameRenameError("");
      hostnameRenameState.preview = null;
      q("hostname-rename-confirm").value = "";
      q("hostname-rename-hardcore").checked = true;
      updateHostnameRenameSaveButton();
    });
    const uploadDropZone = q("storage-fm-upload-dropzone");
    const uploadPicker = q("storage-fm-upload-picker");
    uploadDropZone.addEventListener("click", () => uploadPicker.click());
    uploadDropZone.addEventListener("dragover", (event) => {
      event.preventDefault();
      uploadDropZone.classList.add("active");
    });
    uploadDropZone.addEventListener("dragleave", () => uploadDropZone.classList.remove("active"));
    uploadDropZone.addEventListener("drop", (event) => {
      event.preventDefault();
      uploadDropZone.classList.remove("active");
      storageFmAddFiles(event.dataTransfer?.files || []);
      run(storageFmUploadAll);
    });
    uploadPicker.addEventListener("change", (event) => {
      storageFmAddFiles(event.target.files || []);
      uploadPicker.value = "";
      run(storageFmUploadAll);
    });
    q("btn-storage-delete-confirm").addEventListener("click", () => run(storageFileManagerDeleteConfirmed));
    q("storageDeleteConfirmModal").addEventListener("hidden.bs.modal", () => {
      storageDeletePendingPaths = [];
      q("storage-delete-confirm-word").value = "";
      q("storage-delete-confirm-wrap").classList.add("d-none");
    });
    q("storageNewFolderModal").addEventListener("hidden.bs.modal", () => {
      q("storage-new-folder-name").value = "";
    });
    q("storageRenameModal").addEventListener("hidden.bs.modal", () => {
      storageRenamePendingPath = "";
      q("storage-rename-current-name").textContent = "-";
      q("storage-rename-new-name").value = "";
    });
    q("btn-storage-modal-mount").addEventListener("click", () => run(async () => {
      if (!selectedStorageDeviceId) return;
      await storageMount(selectedStorageDeviceId);
      openStorageDeviceModal(selectedStorageDeviceId);
    }));
    q("btn-storage-modal-unmount").addEventListener("click", () => run(async () => {
      if (!selectedStorageDeviceId) return;
      await storageUnmount(selectedStorageDeviceId);
      openStorageDeviceModal(selectedStorageDeviceId);
    }));
    q("btn-storage-modal-enabled").addEventListener("click", () => run(async () => {
      if (!selectedStorageDeviceId) return;
      const item = knownStorageById.get(selectedStorageDeviceId);
      if (!item) return;
      await storageToggleEnabled(selectedStorageDeviceId, !item.is_enabled);
      openStorageDeviceModal(selectedStorageDeviceId);
    }));
    q("btn-storage-modal-automount").addEventListener("click", () => run(async () => {
      if (!selectedStorageDeviceId) return;
      const item = knownStorageById.get(selectedStorageDeviceId);
      if (!item) return;
      await storageToggleAutoMount(selectedStorageDeviceId, !item.auto_mount);
      openStorageDeviceModal(selectedStorageDeviceId);
    }));
    q("btn-storage-modal-format").addEventListener("click", () => run(async () => {
      if (!selectedStorageDeviceId) return;
      const item = knownStorageById.get(selectedStorageDeviceId);
      await storageFormat(selectedStorageDeviceId, item?.label || item?.name || "");
      openStorageDeviceModal(selectedStorageDeviceId);
    }));
    q("btn-storage-modal-remove").addEventListener("click", () => run(async () => {
      if (!selectedStorageDeviceId) return;
      await storageRemove(selectedStorageDeviceId);
      const modal = bootstrap.Modal.getOrCreateInstance(q("storageDeviceModal"));
      modal.hide();
      selectedStorageDeviceId = "";
    }));
    q("btn-ap-enable").addEventListener("click", () => run(() => toggleAp(true)));
    q("btn-ap-disable").addEventListener("click", () => run(() => toggleAp(false)));
    q("btn-ap-refresh").addEventListener("click", () => run(async () => {
      await refreshApStatus();
      await refreshApClients();
    }));
    q("btn-system-update-portal").addEventListener("click", () => run(updatePortal));
    q("btn-status-shutdown").addEventListener("click", () => run(() => requestSystemPower("shutdown")));
    q("btn-status-reboot").addEventListener("click", () => run(() => requestSystemPower("reboot")));
    q("btn-status-restart-portal").addEventListener("click", () => run(restartPortalServiceNow));
    const heroUpdateBtn = q("hero-update-btn");
    if (heroUpdateBtn) {
      heroUpdateBtn.addEventListener("click", () => openUpdateTab());
    }
    q("btn-fix-tailscale-dns").addEventListener("click", () => run(fixTailscaleDns));
    q("btn-system-refresh-network").addEventListener("click", () => run(refreshNetwork));
    q("btn-system-refresh-network-radio").addEventListener("click", () => run(refreshNetwork));
    q("btn-overlay-refresh").addEventListener("click", () => run(refreshOverlayState));
    q("btn-overlay-save-flash").addEventListener("click", () => run(saveOverlayFlash));
    q("btn-overlay-save-ticker").addEventListener("click", () => run(saveOverlayTicker));
    q("btn-overlay-save-popup").addEventListener("click", () => run(saveOverlayPopup));
    q("btn-overlay-clear-flash").addEventListener("click", () => run(() => clearOverlayCategory("flash")));
    q("btn-overlay-clear-tickers").addEventListener("click", () => run(() => clearOverlayCategory("tickers")));
    q("btn-overlay-clear-popups").addEventListener("click", () => run(() => clearOverlayCategory("popups")));
    q("btn-overlay-reset-all").addEventListener("click", () => run(resetOverlayState));

    q("btn-confirm-unlink").addEventListener("click", async () => {
      const modal = bootstrap.Modal.getOrCreateInstance(q("unlinkModal"));
      modal.hide();
      await run(panelUnlink);
    });

    q("setup-wizard-next").addEventListener("click", () => {
      wizardNext().catch((err) => {
        setSetupError(err && err.message ? err.message : String(err || "Unbekannter Fehler"));
      });
    });
    q("setup-wizard-back").addEventListener("click", () => wizardBack());
    q("setup-finish-now").addEventListener("click", () => run(() => completeSetupWizard(false)));
    q("setup-go-step-3").addEventListener("click", () => {
      setupWizardState.step = 3;
      setSetupError("");
      updateSetupSearchUi();
      updateSetupStepDots();
      updateSetupPanels();
      updateSetupFooterButtons();
    });
    q("setup-wizard-complete").addEventListener("click", async () => {
      setSetupError("");
      setSetupBusy(true);
      try {
        await wizardAssignSelection();
        await completeSetupWizard(false);
      } catch (err) {
        setSetupError(err && err.message ? err.message : String(err || "Unbekannter Fehler"));
      } finally {
        setSetupBusy(false);
      }
    });
    q("setup-wizard-skip-complete").addEventListener("click", async () => {
      try {
        await completeSetupWizard(true);
      } catch (err) {
        setSetupError(err && err.message ? err.message : String(err || "Unbekannter Fehler"));
      }
    });
    q("setupWizardModal").addEventListener("hidden.bs.modal", () => {
      setSetupError("");
      if (setupWizardState.searchTimer) {
        window.clearTimeout(setupWizardState.searchTimer);
        setupWizardState.searchTimer = null;
      }
      setupWizardState.searchSeq += 1;
    });
    q("btPairingModal").addEventListener("hidden.bs.modal", () => {
      if (btPairingPollHandle) {
        window.clearInterval(btPairingPollHandle);
        btPairingPollHandle = null;
      }
    });
    for (const listId of ["security-trusted-wifi", "security-trusted-lan", "security-trusted-bt"]) {
      q(listId).addEventListener("click", (event) => {
        const btn = event.target && event.target.closest ? event.target.closest("button[data-kind][data-key]") : null;
        if (!btn) return;
        const kind = String(btn.dataset.kind || "").trim();
        const key = String(btn.dataset.key || "").trim();
        if (!kind || !key) return;
        run(() => removeTrustedNetworkMarker(kind, key));
      });
    }
    q("sentinel-list").addEventListener("click", (event) => {
      const btn = event.target && event.target.closest ? event.target.closest("button[data-action][data-slug]") : null;
      if (!btn) return;
      const action = String(btn.dataset.action || "").trim();
      const slug = String(btn.dataset.slug || "").trim();
      if (!slug) return;
      if (action === "install") {
        run(() => installSentinel(slug));
        return;
      }
      if (action === "uninstall") {
        run(() => uninstallSentinel(slug));
      }
    });
    for (const radio of document.querySelectorAll('input[name="setup-link-type"]')) {
      radio.addEventListener("change", () => {
        setupWizardState.linkType = getSetupLinkType();
        setupWizardState.selectedLinkItem = null;
        q("setup-link-selection").textContent = "Keine Auswahl.";
        renderSetupSearchResults([]);
        const status = q("setup-link-search-status");
        if (status) status.textContent = "Mindestens 2 Zeichen eingeben.";
        updateSetupSearchUi();
        updateSetupFooterButtons();
      });
    }
    q("setup-link-search").addEventListener("input", () => {
      if (setupWizardState.searchTimer) {
        window.clearTimeout(setupWizardState.searchTimer);
      }
      setupWizardState.selectedLinkItem = null;
      q("setup-link-selection").textContent = "Keine Auswahl.";
      const query = q("setup-link-search").value || "";
      setupWizardState.searchTimer = window.setTimeout(() => {
        wizardSearchLinks(query).catch((err) => {
          setSetupError(err && err.message ? err.message : String(err || "Unbekannter Fehler"));
        });
      }, 280);
    });
  }

  async function run(fn) {
    try {
      await fn();
    } catch (err) {
      toast(err.message || String(err), "danger");
    }
  }

  function initRefs() {
    els.alertHost = q("alert-host");
    els.heroStatus = q("hero-status");
    els.adminBase = q("admin-base-url");
    els.regToken = q("registration-token");
    els.deviceSlug = q("device-slug");
    els.streamSlug = q("stream-slug");
    els.btnWifiToggle = q("btn-wifi-toggle");
    els.btnBtToggle = q("btn-bt-toggle");
    els.btnBtPairingStart = q("btn-bt-pairing-start");
    els.btnLanToggle = q("btn-lan-toggle");
  }

  async function boot() {
    initRefs();
    bindButtons();
    await run(refreshStatus);
    await run(refreshPanelFlagsLive);
    await run(refreshSyncStatus);
    await run(panelSyncCheck);
    await run(refreshNetwork);
    await run(refreshWifiScan);
    await run(refreshWifiProfiles);
    await run(refreshWpsStatus);
    await run(refreshWifiLogs);
    await run(refreshStorageStatus);
    await run(refreshStreamOverview);
    await run(refreshStreamAudioFiles);
    await run(refreshStreamAudioStatus);
    await run(loadPlayerRepoConfig);
    await run(() => pollStreamPlayerUpdateStatus(""));
    await run(refreshApStatus);
    await run(refreshApClients);
    await run(refreshSentinelStatus);
    await run(loadLastPortalUpdateStatus);
    await run(refreshOverlayState);
    flushPersistedUpdateResultFlash();
    startApPolling();
    startStoragePolling();
    setWpsTarget(null);
  }

  window.addEventListener("DOMContentLoaded", boot);
})();
