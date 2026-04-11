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
  let _updateModalInstance = null;
  let _updateModalActive = false;
  let _updateModalStartTs = null;
  let _pendingHistoryMeta = null;
  let streamAudioVolumeDebounceHandle = null;
  let streamAudioLastNonZeroVolume = 65;
  let streamAudioMuted = false;
  let currentSystemUpdateSummary = null;
  let llmManagerState = { info: null, repo: null, update: null, didAutoRefresh: false };
  let managedInstallRepos = [];
  let autodiscoverServices = [];
  let repoUpdatesState = { has_updates: false, update_count: 0, checked_at: "", items: [] };
  let repoUpdatesIndex = new Map();
  const streamFeatureState = {
    hasAudioPlayer: false,
    hasDisplayPlayer: false,
    hasDevicePlayer: false,
    playerInstalled: false,
    spotifyInstalled: false,
  };
  const repoInstallPathState = {
    currentPath: "",
    parentPath: "",
    rootPath: "",
    roots: [],
    modal: null,
    targetInputId: "extra-repo-install-dir",
  };
  let _repoChangePathRepoId = null;
  const DEFAULT_PLAYER_SERVICE_NAME = "joormann-media-jarvis-displayplayer.service";
  const streamAudioBrowserState = {
    currentPath: "",
    parentPath: "",
    rootPath: "/mnt",
    modal: null,
  };
  let audioHubPollHandle = null;
  const audioMixerState = {
    lastPayload: null,
    channelDebounce: new Map(),
    micDebounce: new Map(),
  };
  let btDeviceState = {
    devices: [],
    scanDevices: [],
    audioOutputs: { current_output: "", available_outputs: [] },
  };
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
      webhookMode: "discord",
      webhookUrl: "",
      internalWebhookUrl: "",
      internalWebhookSecret: "",
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
    nodeType: "raspi_node",
    registrationTarget: "",
    verifiedUrl: false,
    registered: false,
    linkType: "skip",
    selectedLinkItem: null,
    searchTimer: null,
    searchSeq: 0,
    existingDetected: false,
  };
  const runtimeWarmupState = {
    viewmodel: null,
    consumedPaths: new Set(),
    modal: null,
    initialized: false,
  };
  const UPDATE_CACHE_KEY = "deviceportal.portal_update_status.v1";
  const UPDATE_RESULT_FLASH_KEY = "deviceportal.portal_update_result_flash.v1";
  const MANAGED_REPOS_CACHE_KEY = "deviceportal.managed_repos_cache.v2";
  const SYSTEM_UPDATE_SUMMARY_CACHE_KEY = "deviceportal.system_update_summary.v1";
  const STORAGE_FM_UPLOAD_MAX_FILE_BYTES = 512 * 1024 * 1024;

  function q(id) {
    return document.getElementById(id);
  }

  function _repoLooksInstalled(item) {
    const source = item && typeof item === "object" ? item : {};
    const status = (source.service_status && typeof source.service_status === "object") ? source.service_status : {};
    const useService = source.use_service !== false;
    if (useService) {
      return !!(status.service_installed || status.service_running);
    }
    if (typeof status.runtime_reachable !== "undefined") {
      return !!status.runtime_reachable;
    }
    return !!status.service_running;
  }

  function _detectRepoKinds(item) {
    const source = item && typeof item === "object" ? item : {};
    const bag = [
      String(source.name || ""),
      String(source.repo_name || ""),
      String(source.repo_link || ""),
      String(source.service_name || ""),
      String(source.install_dir || ""),
    ].join(" ").toLowerCase();
    return {
      audio: (
        bag.includes("jarvis-audioplayer")
        || bag.includes("jarvis audioplayer")
        || bag.includes("joormann-media-jarvis-audioplayer")
        || bag.includes("audio player")
      ),
      display: bag.includes("jarvis-displayplayer") || bag.includes("joormann-media-jarvis-displayplayer"),
      device: bag.includes("deviceplayer") || bag.includes("joormann-media-deviceplayer"),
    };
  }

  function recomputeStreamFeatureState() {
    let hasAudioPlayer = false;
    let hasDisplayPlayer = false;
    let hasDevicePlayer = false;
    const consume = (entry) => {
      const kinds = _detectRepoKinds(entry);
      const installed = _repoLooksInstalled(entry) || String(entry?.source || "").toLowerCase() === "autodiscover";
      if (!installed) return;
      if (kinds.audio) hasAudioPlayer = true;
      if (kinds.display) hasDisplayPlayer = true;
      if (kinds.device) hasDevicePlayer = true;
    };
    for (const item of (Array.isArray(managedInstallRepos) ? managedInstallRepos : [])) consume(item);
    for (const item of (Array.isArray(autodiscoverServices) ? autodiscoverServices : [])) consume(item);
    streamFeatureState.hasAudioPlayer = hasAudioPlayer;
    streamFeatureState.hasDisplayPlayer = hasDisplayPlayer;
    streamFeatureState.hasDevicePlayer = hasDevicePlayer;
  }

  function toggleVisible(el, visible) {
    if (!el) return;
    el.classList.toggle("d-none", !visible);
  }

  function applyStreamFeatureVisibility() {
    const hasStream = !!(streamFeatureState.hasDisplayPlayer || streamFeatureState.hasDevicePlayer || streamFeatureState.playerInstalled);
    const hasAudio = !!streamFeatureState.hasAudioPlayer;
    const hasSpotify = !!streamFeatureState.spotifyInstalled;
    const anyVisible = hasStream || hasAudio || hasSpotify;

    toggleVisible(q("stream-feature-stream-col"), hasStream);
    toggleVisible(q("stream-feature-audio-col"), hasAudio);
    toggleVisible(q("stream-feature-spotify-col"), hasSpotify);
    toggleVisible(q("stream-feature-row"), anyVisible);
    toggleVisible(q("stream-feature-empty"), !anyVisible);
  }

  function isNearBottom(el, thresholdPx = 28) {
    if (!el) return true;
    return (el.scrollTop + el.clientHeight) >= (el.scrollHeight - thresholdPx);
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
        throw new Error(`Zeitüberschreitung nach ${seconds}s. Server-Anfrage hat zu lange gedauert.`);
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

  function deepGet(obj, path) {
    let current = obj;
    for (const key of path) {
      if (!current || typeof current !== "object" || !(key in current)) {
        return null;
      }
      current = current[key];
    }
    return current;
  }

  function readWarmupData(path) {
    const model = runtimeWarmupState.viewmodel;
    if (!model || typeof model !== "object") return null;
    return deepGet(model, path);
  }

  function consumeWarmupData(path) {
    const token = path.join(".");
    if (runtimeWarmupState.consumedPaths.has(token)) return null;
    const value = readWarmupData(path);
    if (value === null || value === undefined) return null;
    runtimeWarmupState.consumedPaths.add(token);
    return value;
  }

  function setRuntimeWarmupProgress(percent, text, subtext = "") {
    const pct = Math.max(0, Math.min(100, Number(percent || 0)));
    const bar = q("runtime-warmup-progress");
    const pctEl = q("runtime-warmup-percent");
    const textEl = q("runtime-warmup-text");
    const subtextEl = q("runtime-warmup-subtext");
    if (bar) {
      bar.style.width = `${pct}%`;
      bar.textContent = `${pct}%`;
    }
    if (pctEl) pctEl.textContent = `${pct}%`;
    if (textEl) textEl.textContent = text || "Runtime-Snapshot wird geladen…";
    if (subtextEl) subtextEl.textContent = subtext || "";
  }

  function forceHideRuntimeWarmupModal() {
    const modalEl = q("runtimeWarmupModal");
    if (!modalEl) return;
    try {
      if (runtimeWarmupState.modal) {
        runtimeWarmupState.modal.hide();
      }
    } catch (_) {
      // Continue with hard DOM fallback.
    }
    modalEl.classList.remove("show");
    modalEl.setAttribute("aria-hidden", "true");
    modalEl.style.display = "none";
    document.body.classList.remove("modal-open");
    document.body.style.removeProperty("padding-right");
    for (const backdrop of document.querySelectorAll(".modal-backdrop")) {
      backdrop.remove();
    }
  }

  function setupSessionCloseLogout() {
    const logoutOnClose = () => {
      try {
        if (navigator && typeof navigator.sendBeacon === "function") {
          navigator.sendBeacon("/logout", new Blob([], { type: "application/x-www-form-urlencoded;charset=UTF-8" }));
          return;
        }
      } catch (_) {
        // Fall through to fetch keepalive.
      }
      try {
        fetch("/logout", {
          method: "POST",
          credentials: "same-origin",
          keepalive: true,
        }).catch(() => {});
      } catch (_) {
        // Ignore client shutdown errors.
      }
    };

    window.addEventListener("pagehide", (event) => {
      if (event && event.persisted) return;
      logoutOnClose();
    });
  }

  function applyWarmupViewModel(viewmodel) {
    const model = viewmodel && typeof viewmodel === "object" ? viewmodel : {};
    const legacy = (model.sections && model.sections.legacy && typeof model.sections.legacy === "object") ? model.sections.legacy : {};
    const status = legacy.status;
    const network = legacy.network;
    const storage = legacy.storage;
    const spotifyConnect = legacy.spotify_connect;
    const requirements = legacy.software_requirements;
    const sentinels = legacy.sentinels_status;
    const updateSummary = legacy.system_update_summary;

    if (status && typeof status === "object") {
      renderStatus(status);
    }
    if (network && typeof network === "object") {
      renderNetwork(network);
    }
    if (storage && typeof storage === "object") {
      renderStorageStatus(storage);
    }
    if (spotifyConnect && typeof spotifyConnect === "object") {
      renderSpotifyConnectStatus(spotifyConnect);
    }
    if (requirements && typeof requirements === "object") {
      renderSoftwareRequirementsSection(requirements);
    }
    if (sentinels && typeof sentinels === "object") {
      applySentinelPayload(sentinels);
    }
    if (updateSummary && typeof updateSummary === "object") {
      renderSystemUpdateSummary(updateSummary);
    }
  }

  async function runRuntimeWarmupFlow() {
    const modalEl = q("runtimeWarmupModal");
    if (!modalEl || runtimeWarmupState.initialized) return;

    runtimeWarmupState.initialized = true;
    setRuntimeWarmupProgress(5, "Runtime-Snapshot wird vorbereitet…", "Session-Cache wird initialisiert.");

    if (window.bootstrap && window.bootstrap.Modal) {
      runtimeWarmupState.modal = bootstrap.Modal.getOrCreateInstance(modalEl);
      runtimeWarmupState.modal.show();
    }

    try {
      await fetchJson("/api/runtime/warmup", {
        method: "POST",
        body: { force: false },
        timeoutMs: 45000,
      });
      setRuntimeWarmupProgress(65, "Runtime-Snapshot wurde erzeugt.", "ViewModel wird geladen.");

      const viewPayload = await fetchJson("/api/runtime/viewmodel", {
        cache: "no-store",
        timeoutMs: 45000,
      });
      runtimeWarmupState.viewmodel = viewPayload.data || null;
      applyWarmupViewModel(runtimeWarmupState.viewmodel);
      setRuntimeWarmupProgress(100, "Portal ist bereit.", "Warmup abgeschlossen.");
    } catch (err) {
      setRuntimeWarmupProgress(100, "Warmup fehlgeschlagen.", "Portal läuft mit Legacy-Live-Calls weiter.");
      toast(err && err.message ? err.message : "Runtime-Warmup fehlgeschlagen.", "danger");
    } finally {
      window.setTimeout(() => {
        forceHideRuntimeWarmupModal();
      }, 260);
      window.setTimeout(() => {
        forceHideRuntimeWarmupModal();
      }, 1200);
    }
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

  function hasUsablePanelFlags(flags) {
    if (!flags || typeof flags !== "object") return false;
    const active = normalizeOptionalBoolean(flags.is_active ?? flags.isActive ?? flags.active);
    const locked = normalizeOptionalBoolean(flags.is_locked ?? flags.isLocked ?? flags.locked);
    return active !== null || locked !== null;
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
      const networkTab = q("network-tab");
      if (networkTab && window.bootstrap && window.bootstrap.Tab) {
        window.bootstrap.Tab.getOrCreateInstance(networkTab).show();
      } else if (networkTab) {
        networkTab.click();
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

  function normalizeRepoLink(value) {
    let out = String(value || "").trim().toLowerCase();
    if (out.endsWith("/")) out = out.slice(0, -1);
    if (out.endsWith(".git")) out = out.slice(0, -4);
    return out;
  }

  function buildRepoUpdateKey(repoLink, installDir, serviceName = "") {
    const install = String(installDir || "").trim().toLowerCase();
    if (install) return `install:${install}`;
    const repo = normalizeRepoLink(repoLink);
    if (repo) return `repo:${repo}`;
    return `service:${String(serviceName || "").trim().toLowerCase()}`;
  }

  function setRepoUpdatesState(payload = {}) {
    const items = Array.isArray(payload.items) ? payload.items : [];
    repoUpdatesState = {
      has_updates: !!payload.has_updates,
      update_count: Number(payload.update_count || 0),
      checked_at: String(payload.checked_at || ""),
      items,
    };
    repoUpdatesIndex = new Map();
    for (const item of items) {
      if (!item || typeof item !== "object") continue;
      const key = String(item.key || "").trim();
      const fallbackKey = buildRepoUpdateKey(item.repo_link, item.install_dir, item.service_name);
      if (key) repoUpdatesIndex.set(key, item);
      if (fallbackKey) repoUpdatesIndex.set(fallbackKey, item);
    }
    if (currentSystemUpdateSummary && typeof currentSystemUpdateSummary === "object") {
      renderSystemUpdateSummary(currentSystemUpdateSummary);
    }
    if (Array.isArray(managedInstallRepos) && managedInstallRepos.length) {
      renderManagedRepos(managedInstallRepos);
    }
    renderHeroUpdateBadge((statusDashboardState.status || {}).app_update || {}, repoUpdatesState);
    const checkedAtEl = q("repo-updates-checked-at");
    const countEl = q("repo-updates-count");
    if (checkedAtEl) checkedAtEl.textContent = repoUpdatesState.checked_at || "-";
    if (countEl) {
      const n = repoUpdatesState.update_count;
      countEl.textContent = n > 0 ? `${n} verfügbar` : (repoUpdatesState.checked_at ? "Keine" : "-");
    }
  }

  function resolveRepoUpdateInfo(source = {}) {
    const key = buildRepoUpdateKey(source.repo_link, source.install_dir, source.service_name);
    if (key && repoUpdatesIndex.has(key)) {
      return repoUpdatesIndex.get(key);
    }
    const repoOnlyKey = buildRepoUpdateKey(source.repo_link, "", source.service_name);
    if (repoOnlyKey && repoUpdatesIndex.has(repoOnlyKey)) {
      return repoUpdatesIndex.get(repoOnlyKey);
    }
    return null;
  }

  function countVisibleRepoUpdates() {
    let count = 0;
    const summary = currentSystemUpdateSummary && typeof currentSystemUpdateSummary === "object" ? currentSystemUpdateSummary : {};
    const portal = (summary.portal && typeof summary.portal === "object") ? summary.portal : null;
    const player = (summary.player && typeof summary.player === "object") ? summary.player : null;
    if (portal) {
      const info = resolveRepoUpdateInfo({
        repo_link: portal.repo,
        install_dir: portal.install_dir,
        service_name: portal.service_name,
      });
      if (info && info.available) count += 1;
    }
    if (player) {
      const info = resolveRepoUpdateInfo({
        repo_link: player.repo,
        install_dir: player.install_dir,
        service_name: player.service_name,
      });
      if (info && info.available) count += 1;
    }
    for (const item of (Array.isArray(managedInstallRepos) ? managedInstallRepos : [])) {
      const info = resolveRepoUpdateInfo(item);
      if (info && info.available) count += 1;
    }
    return count;
  }

  function renderHeroUpdateBadge(statusUpdate = {}, repoUpdates = {}) {
    const updateBadge = q("hero-update");
    if (!updateBadge) return;
    updateBadge.classList.remove("text-bg-danger", "text-bg-secondary", "text-bg-warning", "text-bg-success");

    const visibleCount = countVisibleRepoUpdates();
    const tabBadge = q("system-update-tab-badge");
    if (tabBadge) {
      if (visibleCount > 0) {
        tabBadge.textContent = String(visibleCount);
        tabBadge.classList.remove("d-none");
      } else {
        tabBadge.classList.add("d-none");
      }
    }
    if (visibleCount > 0) {
      updateBadge.classList.add("text-bg-warning");
      updateBadge.textContent = `Updates verfügbar (${visibleCount})`;
      return;
    }

    if (repoUpdates && Array.isArray(repoUpdates.items)) {
      updateBadge.classList.add("text-bg-success");
      updateBadge.textContent = "Keine Updates";
      return;
    }

    const localVersion = updateLocalVersion(statusUpdate);
    if (statusUpdate.available) {
      updateBadge.classList.add("text-bg-warning");
      const shortRemote = updateRemoteShort(statusUpdate);
      updateBadge.textContent = `Update verfügbar (${localVersion} -> ${shortRemote})`;
    } else if (statusUpdate.error) {
      updateBadge.classList.add("text-bg-secondary");
      updateBadge.textContent = "Update-Check nicht verfügbar";
    } else {
      updateBadge.classList.add("text-bg-success");
      updateBadge.textContent = `Up to date (${localVersion})`;
    }
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

  function escapeHtml(value) {
    return String(value || "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "\"": "&quot;",
      "'": "&#39;",
    }[ch] || ch));
  }

  function softwareReqActionHtml(item) {
    if (!item || typeof item !== "object") return '<span class="text-secondary">-</span>';
    const key = escapeHtml(item.key || "");
    const installBtn = (!item.installed && item.installable)
      ? `<button type="button" class="btn btn-sm btn-outline-primary js-software-req-action" data-action="install" data-key="${key}">Installieren</button>`
      : "";
    const startBtn = (item.installed && item.startable)
      ? `<button type="button" class="btn btn-sm btn-outline-success js-software-req-action" data-action="start" data-key="${key}">Starten</button>`
      : "";
    const actions = [installBtn, startBtn].filter(Boolean).join(" ");
    return actions || '<span class="text-secondary">-</span>';
  }

  function renderSoftwareRequirementsSection(data) {
    const tableHost = q("software-req-table");
    const summary = q("software-req-summary");
    if (!tableHost || !summary) return;
    const items = Array.isArray(data && data.items) ? data.items : [];
    const installed = Number(data && data.installed || 0);
    const total = Number(data && data.total || items.length || 0);
    const missing = Number(data && data.missing || Math.max(0, total - installed));
    summary.textContent = `${installed}/${total} vorhanden, ${missing} fehlen.`;
    const rows = items.map((item) => {
      const ok = Boolean(item && item.installed);
      const badge = ok
        ? '<span class="badge text-bg-success">Installiert</span>'
        : '<span class="badge text-bg-danger">Fehlt</span>';
      return `<tr><td>${escapeHtml(item.label)}</td><td>${badge}</td><td class="text-secondary">${escapeHtml(item.version)}</td><td class="text-secondary">${escapeHtml(item.runtime)}</td><td class="text-secondary">${escapeHtml(item.detail)}</td><td>${softwareReqActionHtml(item)}</td></tr>`;
    }).join("");
    tableHost.innerHTML = `<table class="table table-sm align-middle mb-0"><thead><tr><th>Komponente</th><th>Status</th><th>Version</th><th>Runtime</th><th>Details</th><th>Aktion</th></tr></thead><tbody>${rows || '<tr><td colspan="6" class="text-secondary">Keine Daten.</td></tr>'}</tbody></table>`;
  }

  async function refreshSoftwareRequirements() {
    const summary = q("software-req-summary");
    const tableHost = q("software-req-table");
    if (!summary || !tableHost) return;
    const warmupData = consumeWarmupData(["sections", "legacy", "software_requirements"]);
    if (warmupData && typeof warmupData === "object") {
      renderSoftwareRequirementsSection(warmupData);
      return warmupData;
    }
    summary.textContent = "Lade...";
    const data = await fetchJson("/api/auth/system-requirements", { cache: "no-store" });
    renderSoftwareRequirementsSection(data);
    return data;
  }

  async function runSoftwareRequirementAction(action, key) {
    const data = await fetchJson("/api/auth/system-requirements/action", {
      method: "POST",
      body: { action, key },
      timeoutMs: action === "install" ? 300000 : 60000,
    });
    renderSoftwareRequirementsSection(data);
    const msg = (((data || {}).result || {}).message || "").trim();
    if (msg) toast(msg, "success");
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
    const screenshots = display.screenshots || {};
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

      const screenshotWrap = document.createElement("div");
      screenshotWrap.className = "status-display-screenshot";
      const shotInfo = connector ? screenshots[connector] : null;
      const isConnected = !!item.connected;
      if (shotInfo && shotInfo.available && shotInfo.url) {
        const img = document.createElement("img");
        img.alt = `Screenshot ${connector || "Display"}`;
        img.src = String(shotInfo.url || "");
        screenshotWrap.append(img);
        if (shotInfo.updated_at) {
          const metaLine = document.createElement("div");
          metaLine.className = "status-display-screenshot-meta";
          metaLine.textContent = `Update: ${shotInfo.updated_at}`;
          screenshotWrap.append(metaLine);
        }
      } else {
        const empty = document.createElement("div");
        empty.className = "status-display-screenshot-empty";
        empty.textContent = isConnected ? "Kein Screenshot" : "Kein Monitor verbunden";
        screenshotWrap.append(empty);
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

      const shotRow = document.createElement("div");
      shotRow.className = "row g-2";
      const shotColPrimary = document.createElement("div");
      shotColPrimary.className = "col-md-6 d-grid";
      const shotBtn = document.createElement("button");
      shotBtn.className = "btn btn-outline-secondary btn-sm";
      shotBtn.textContent = "Screenshot neu";
      shotBtn.disabled = !connector || !isConnected;
      shotBtn.addEventListener("click", () => {
        run(async () => {
          shotBtn.disabled = true;
          try {
            await fetchJson(`/api/display/screenshot/${encodeURIComponent(connector)}/capture`, { method: "POST" });
            await refreshStatus();
            toast("Screenshot erstellt", "success");
          } finally {
            shotBtn.disabled = !connector;
          }
        });
      });
      shotColPrimary.append(shotBtn);

      const shotColSecondary = document.createElement("div");
      shotColSecondary.className = "col-md-6 d-grid";
      const shotDel = document.createElement("button");
      shotDel.className = "btn btn-outline-danger btn-sm";
      shotDel.textContent = "Screenshot löschen";
      shotDel.disabled = !connector || !isConnected;
      shotDel.addEventListener("click", () => {
        run(async () => {
          shotDel.disabled = true;
          try {
            await fetchJson(`/api/display/screenshot/${encodeURIComponent(connector)}/delete`, { method: "POST" });
            await refreshStatus();
            toast("Screenshot gelöscht", "success");
          } finally {
            shotDel.disabled = !connector;
          }
        });
      });
      shotColSecondary.append(shotDel);

      shotRow.append(shotColPrimary, shotColSecondary);

      row.append(colSelect, colToggle, colSave);
      actions.append(row);
      actions.append(shotRow);

      card.append(head, kv, screenshotWrap, actions);
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
    const nodeRolesRaw = (config && typeof config === "object") ? config.panel_node_roles : null;

    const readFlag = (...keys) => {
      for (const key of keys) {
        if (Object.prototype.hasOwnProperty.call(flags, key)) {
          return normalizeOptionalBoolean(flags[key]);
        }
      }
      return null;
    };

    const renderSwitch = (value, wrapId, badgeId, valueId, onText, offText, trueClass, falseClass) => {
      const wrap = q(wrapId);
      const badge = q(badgeId);
      const valueEl = q(valueId);
      if (!wrap || !badge || !valueEl) return;
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

    const isActive = readFlag("is_active", "isActive", "active");
    const isLocked = readFlag("is_locked", "isLocked", "locked");
    renderSwitch(isActive, "hero-flag-active-wrap", "hero-flag-active-badge", "hero-flag-active-value", "active", "inactive", "text-bg-success", "text-bg-warning");
    renderSwitch(isLocked, "hero-flag-locked-wrap", "hero-flag-locked-badge", "hero-flag-locked-value", "locked", "unlocked", "text-bg-danger", "text-bg-success");

    const updatedEl = q("hero-flag-updated");
    if (updatedEl) {
      updatedEl.textContent = String(flags.updated_at || flags.updatedAt || "-");
    }

    const rolesEl = q("hero-node-roles");
    if (rolesEl) {
      const normalized = [];
      const source = Array.isArray(nodeRolesRaw) ? nodeRolesRaw : [];
      for (const roleRaw of source) {
        const role = String(roleRaw || "").trim().toLowerCase();
        if (!role) continue;
        if (!["raspi", "hardware", "jarvis", "smarthome"].includes(role)) continue;
        if (!normalized.includes(role)) normalized.push(role);
      }
      const labels = {
        raspi: "Raspi",
        hardware: "Hardware",
        jarvis: "Jarvis",
        smarthome: "Smarthome",
      };
      rolesEl.textContent = normalized.length ? normalized.map((r) => labels[r] || r).join(" | ") : "-";
    }
  }

  function getSetupLinkType() {
    const checked = document.querySelector('input[name="setup-link-type"]:checked');
    return checked ? String(checked.value || "skip") : "skip";
  }

  function getSetupNodeType() {
    const el = q("setup-node-type");
    const value = String(el && el.value ? el.value : "raspi_node").trim().toLowerCase();
    if (value === "server" || value === "workstation" || value === "raspi_node") return value;
    return "raspi_node";
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
    setupWizardState.nodeType = String(cfg.node_runtime_type || "raspi_node").trim().toLowerCase() || "raspi_node";
    setupWizardState.registrationTarget = String(cfg.panel_register_target || "").trim().toLowerCase();
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
    q("setup-node-type").value = setupWizardState.nodeType;
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
    setupWizardState.nodeType = getSetupNodeType();
    if (!setupWizardState.token) {
      throw new Error("Bitte einen Registrierungstoken eingeben.");
    }
    const tokenValidateTimeoutMs = setupWizardState.nodeType === "raspi_node"
      ? REQUEST_TIMEOUTS.panelTokenValidateMs
      : REQUEST_TIMEOUTS.panelRegisterMs;
    const validatePayload = await fetchJson("/api/panel/validate-token", {
      method: "POST",
      body: {
        admin_base_url: setupWizardState.panelUrl || q("setup-panel-url").value || "",
        registration_token: setupWizardState.token,
        node_type: setupWizardState.nodeType,
      },
      timeoutMs: tokenValidateTimeoutMs,
    });
    if (!validatePayload.valid) {
      throw new Error("Token ist ungültig.");
    }
    setupWizardState.registrationTarget = String(validatePayload.registration_target || "").trim().toLowerCase();
    const registerEndpoint = "/api/panel/register";
    const registerPayload = await fetchJson(registerEndpoint, {
      method: "POST",
      body: {
        admin_base_url: setupWizardState.panelUrl || q("setup-panel-url").value || "",
        registration_token: setupWizardState.token,
        node_type: setupWizardState.nodeType,
        registration_target: setupWizardState.registrationTarget || undefined,
      },
      timeoutMs: setupWizardState.nodeType === "raspi_node" ? REQUEST_TIMEOUTS.panelTokenValidateMs : REQUEST_TIMEOUTS.panelRegisterMs,
    });
    setupWizardState.registered = !!registerPayload.ok;
    const serverResp = registerPayload && typeof registerPayload.response === "object" ? registerPayload.response : {};
    const serverDevice = serverResp && typeof serverResp.device === "object" ? serverResp.device : {};
    const rotatedToken = String(
      serverDevice.registerToken
      || serverResp.registerToken
      || (serverResp.data && typeof serverResp.data === "object" ? serverResp.data.registerToken : "")
      || ""
    ).trim();
    if (rotatedToken) {
      setupWizardState.token = rotatedToken;
      q("setup-registration-token").value = rotatedToken;
    }
    if (els.regToken) els.regToken.value = setupWizardState.token;
    const result = q("setup-step-2-result");
    if (result) {
      const h = registerPayload.http || "-";
      const targetLabel = setupWizardState.registrationTarget === "jarvis"
        ? "Jarvis AI Hub"
        : (setupWizardState.registrationTarget === "hardware" ? "Hardware Node" : "Smarthome Node");
      result.textContent = `Gerät erfolgreich verknüpft (${targetLabel}) via ${registerEndpoint} (HTTP ${h}).`;
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
      node_type: setupWizardState.nodeType,
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
        node_type: setupWizardState.nodeType,
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
    const repoUpdates = (data.repo_updates && typeof data.repo_updates === "object") ? data.repo_updates : {};
    statusDashboardState.status = data;
    setRepoUpdatesState(repoUpdates);

    setStatusBadge(linked, online);
    renderHeroPanelFlags(cfg);
    renderHeroUpdateBadge(update, repoUpdates);
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
    portalSecurityState.sentinels.webhookMode = String(rawSentinel.webhook_mode || "discord");
    portalSecurityState.sentinels.webhookUrl = String(rawSentinel.webhook_url || "");
    portalSecurityState.sentinels.internalWebhookUrl = String(rawSentinel.internal_webhook_url || "");
    portalSecurityState.sentinels.internalWebhookSecret = String(rawSentinel.internal_webhook_secret || "");
    const webhookInput = q("sentinel-webhook-url");
    const webhookModeInput = q("sentinel-webhook-mode");
    const internalWebhookInput = q("sentinel-internal-webhook-url");
    const internalWebhookSecretInput = q("sentinel-internal-webhook-secret");
    if (webhookModeInput && !webhookModeInput.value) {
      webhookModeInput.value = portalSecurityState.sentinels.webhookMode;
    }
    if (webhookInput && !webhookInput.value) {
      webhookInput.value = portalSecurityState.sentinels.webhookUrl;
    }
    if (internalWebhookInput && !internalWebhookInput.value) {
      internalWebhookInput.value = portalSecurityState.sentinels.internalWebhookUrl;
    }
    if (internalWebhookSecretInput && !internalWebhookSecretInput.value) {
      internalWebhookSecretInput.value = portalSecurityState.sentinels.internalWebhookSecret;
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
    const capabilities = (data.capabilities && typeof data.capabilities === "object") ? data.capabilities : {};
    const wifiCap = (capabilities.wifi && typeof capabilities.wifi === "object") ? capabilities.wifi : { available: true };
    const btCap = (capabilities.bluetooth && typeof capabilities.bluetooth === "object") ? capabilities.bluetooth : { available: true };
    const lanCap = (capabilities.lan && typeof capabilities.lan === "object") ? capabilities.lan : { available: true };
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
    q("wifi-enabled").textContent = wifiCap.available ? yn(!!wifi.enabled) : "n/a (kein Modul)";
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

    q("bt-enabled").textContent = btCap.available ? yn(!!bt.enabled) : "n/a (kein Modul)";
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
    if (els.btnWifiToggle) {
      els.btnWifiToggle.disabled = !wifiCap.available;
      els.btnWifiToggle.title = wifiCap.available ? "" : "Kein WLAN-Interface erkannt";
    }
    if (els.btnBtToggle) {
      els.btnBtToggle.disabled = !btCap.available;
      els.btnBtToggle.title = btCap.available ? "" : "Kein Bluetooth-Adapter erkannt";
    }
    for (const id of ["btn-bt-pairing-start", "btn-bt-scan", "btn-bt-devices-refresh"]) {
      const btn = q(id);
      if (!btn) continue;
      btn.disabled = !btCap.available;
      btn.title = btCap.available ? "" : "Kein Bluetooth-Adapter erkannt";
    }
    if (els.btnLanToggle) {
      els.btnLanToggle.disabled = !lanCap.available;
      els.btnLanToggle.title = lanCap.available ? "" : "Keine LAN-Schnittstelle erkannt";
    }
    for (const id of [
      "btn-wifi-scan",
      "btn-wifi-profiles-refresh",
      "btn-wifi-profiles-apply",
      "btn-wifi-manual-add",
      "btn-wps-start",
      "btn-wps-target-clear",
    ]) {
      const btn = q(id);
      if (!btn) continue;
      btn.disabled = !wifiCap.available;
      btn.title = wifiCap.available ? "" : "WLAN-Interface nicht vorhanden";
    }
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
    const llmInfo = (data.config && typeof data.config === "object") ? data.config.llm_manager : null;
    renderLlmManagerCard(data, llmInfo);
  }

  function isWifiAvailable() {
    const caps = ((networkState || {}).capabilities || {});
    const wifi = (caps.wifi && typeof caps.wifi === "object") ? caps.wifi : null;
    if (!wifi) return true;
    return !!wifi.available;
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
      if (hasUsablePanelFlags(payload.panel_device_flags) && statusDashboardState.status && statusDashboardState.status.config) {
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
    const warmupData = consumeWarmupData(["sections", "legacy", "status"]);
    if (warmupData && typeof warmupData === "object") {
      renderStatus(warmupData);
      runQuiet(refreshLlmManagerInfo, ["login_required", "llm_manager_unavailable"]);
      return warmupData;
    }
    const data = await fetchJson("/api/status", { cache: "no-store" });
    renderStatus(data);
    runQuiet(refreshLlmManagerInfo, ["login_required", "llm_manager_unavailable"]);
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
    const warmupData = consumeWarmupData(["sections", "legacy", "network"]);
    if (warmupData && typeof warmupData === "object") {
      renderNetwork(warmupData);
      return warmupData;
    }
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
    const warmupData = consumeWarmupData(["sections", "legacy", "storage"]);
    if (warmupData && typeof warmupData === "object") {
      renderStorageStatus(warmupData);
      return warmupData;
    }
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
    const available = data.available !== false;
    const active = !!data.active;
    q("ap-ssid").textContent = available ? (data.ssid || "-") : "n/a";
    q("ap-ip").textContent = available ? (data.ip || "-") : "n/a";
    q("ap-portal-url").textContent = available ? (data.portal_url || (data.ip ? `http://${data.ip}` : "-")) : "n/a";
    q("ap-profile").textContent = data.profile || "jm-hotspot";
    q("ap-clients-count").textContent = String(data.clients_count || 0);
    const badge = q("ap-active-badge");
    badge.classList.remove("text-bg-success", "text-bg-secondary");
    badge.classList.add(active ? "text-bg-success" : "text-bg-secondary");
    badge.textContent = available ? (active ? "aktiv" : "inaktiv") : "nicht verfügbar";
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
    if (!isWifiAvailable()) {
      renderApStatus({ data: { active: false, ssid: "-", ip: "-", portal_url: "-", profile: "jm-hotspot", clients_count: 0, available: false } });
      return { available: false };
    }
    const payload = await fetchJson("/api/network/ap/status");
    renderApStatus(payload);
    return payload;
  }

  async function refreshApClients() {
    if (!isWifiAvailable()) {
      renderApClients([]);
      return { available: false, data: { clients: [] } };
    }
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

  function renderWifiScan(networks, options = {}) {
    const deduped = collapseMeshScanNetworks(networks);
    const host = q("wifi-scan-list");
    clearNode(host);
    if (options && options.unavailable) {
      const empty = document.createElement("div");
      empty.className = "text-secondary";
      empty.textContent = options.message || "WLAN Modul/Interface nicht verfügbar.";
      host.append(empty);
      return;
    }
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
    if (!isWifiAvailable()) {
      renderWifiScan([], { unavailable: true, message: "WLAN-Interface nicht vorhanden. Scan übersprungen." });
      return { available: false, networks: [] };
    }
    const payload = await fetchJson("/api/wifi/scan");
    const data = payload.data || {};
    if (data.available === false) {
      renderWifiScan([], { unavailable: true, message: "WLAN-Interface nicht vorhanden. Scan nicht möglich." });
      return data;
    }
    renderWifiScan(data.networks || [], {});
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
    if (!isWifiAvailable()) {
      const host = q("wifi-profiles-list");
      if (host) {
        clearNode(host);
        const empty = document.createElement("div");
        empty.className = "text-secondary";
        empty.textContent = "WLAN-Interface nicht vorhanden. Profile-Verwaltung deaktiviert.";
        host.append(empty);
      }
      return { ok: true, data: { profiles: [], unavailable: true } };
    }
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
    if (!isWifiAvailable()) {
      q("wifi-wps-phase").textContent = "unsupported - WLAN-Interface nicht vorhanden.";
      return { available: false, wps: { phase: "unsupported", phase_message: "WLAN-Interface nicht vorhanden." } };
    }
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
    const cfg = (statusDashboardState.status || {}).config || {};
    await fetchJson("/api/panel/register", {
      method: "POST",
      body: {
        admin_base_url: adminBaseUrl,
        registration_token: els.regToken.value || "",
        node_type: cfg.node_runtime_type || "raspi_node",
        registration_target: cfg.panel_register_target || undefined,
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
      const hasOwnOk = Object.prototype.hasOwnProperty.call(syncData, "ok");
      const syncOk = hasOwnOk ? !!syncData.ok : !!payload.ok;
      const normalizedSyncData = hasOwnOk ? syncData : { ...syncData, ok: syncOk };
      if (hasUsablePanelFlags(payload.panel_device_flags) && statusDashboardState.status && statusDashboardState.status.config) {
        statusDashboardState.status.config.panel_device_flags = payload.panel_device_flags;
        renderHeroPanelFlags(statusDashboardState.status.config);
      }
      renderPanelSyncStatus(normalizedSyncData, false);
      toast(syncOk ? "Admin-Sync vollständig" : "Admin-Sync unvollständig", syncOk ? "success" : "warning");
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
    const spotifyConnect = data.spotify_connect || null;

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

    const playerRunning = isServiceActive(player);
    const playerStatusEl = q("stream-player-status");
    if (playerStatusEl) {
      if (player && player.error) {
        playerStatusEl.textContent = `Status: Fehler (${player.error})`;
      } else if (player && typeof player.active !== "undefined") {
        playerStatusEl.textContent = `Status: ${playerRunning ? "aktiv" : "inaktiv"} (${player.substate || "-"})`;
      } else {
        playerStatusEl.textContent = "Status: -";
      }
    }
    streamFeatureState.playerInstalled = !!(player.service_installed || playerRunning);
    applyStreamFeatureVisibility();

    if (spotifyConnect && typeof spotifyConnect === "object") {
      renderSpotifyConnectStatus(spotifyConnect);
    }
  }

  function renderSpotifyConnectStatus(payload) {
    const data = (payload && payload.data && typeof payload.data === "object") ? payload.data : payload || {};
    const installed = !!data.serviceInstalled;
    const enabled = !!data.serviceEnabled;
    const running = !!data.serviceRunning;
    const ready = !!data.connectReady;
    const serviceName = String(data.serviceName || "-");
    const serviceScope = String(data.serviceScope || "-");
    const deviceName = String(data.deviceName || "-");
    const backend = String(data.backend || "-");
    const output = String(data.outputDevice || "-");
    const checkedAt = String(data.checkedAt || "-");
    const lastError = String(data.lastError || "").trim();
    const activeState = String(data.serviceActiveState || "-");
    const subState = String(data.serviceSubState || "-");

    const statusEl = q("spotify-connect-status");
    if (statusEl) {
      statusEl.textContent = `Status: ${running ? "läuft" : "inaktiv"} (${activeState}/${subState})`;
    }

    q("spotify-connect-service-name").textContent = serviceName;
    q("spotify-connect-service-scope").textContent = serviceScope || "-";
    q("spotify-connect-installed").textContent = installed ? "ja" : "nein";
    q("spotify-connect-enabled").textContent = enabled ? "ja" : "nein";
    q("spotify-connect-running").textContent = running ? "ja" : "nein";
    q("spotify-connect-device-name").textContent = deviceName || "-";
    q("spotify-connect-backend").textContent = backend || "-";
    q("spotify-connect-output").textContent = output || "-";
    q("spotify-connect-checked-at").textContent = checkedAt || "-";
    q("spotify-connect-last-error").textContent = lastError || "—";

    const badge = q("spotify-connect-ready-badge");
    if (badge) {
      badge.classList.remove("text-bg-success", "text-bg-warning", "text-bg-secondary", "text-bg-danger");
      if (!installed) {
        badge.classList.add("text-bg-secondary");
        badge.textContent = "not installed";
      } else if (ready) {
        badge.classList.add("text-bg-success");
        badge.textContent = "connect ready";
      } else if (running) {
        badge.classList.add("text-bg-warning");
        badge.textContent = "running";
      } else {
        badge.classList.add("text-bg-danger");
        badge.textContent = "not ready";
      }
    }
    streamFeatureState.spotifyInstalled = installed;
    applyStreamFeatureVisibility();
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
    const playerRunning = isServiceActive(player);
    streamFeatureState.playerInstalled = !!(player.service_installed || playerRunning);
    const playerStatusEl = q("stream-player-status");
    if (playerStatusEl) {
      if (player && player.error) {
        playerStatusEl.textContent = `Status: Fehler (${player.error})`;
      } else if (typeof player.active !== "undefined") {
        playerStatusEl.textContent = `Status: ${playerRunning ? "aktiv" : "inaktiv"} (${player.substate || "-"})`;
      } else {
        playerStatusEl.textContent = "Status: -";
      }
    }
    applyStreamFeatureVisibility();
  }

  async function refreshSpotifyConnectStatus() {
    const payload = await fetchJson("/api/spotify-connect/status", { cache: "no-store" });
    renderSpotifyConnectStatus(payload);
    return payload;
  }

  async function refreshSpotifyConnectConfig() {
    const payload = await fetchJson("/api/spotify-connect/config", { cache: "no-store" });
    const data = payload.data || {};
    q("spotify-connect-config-name").value = String(data.service_name || "");
    q("spotify-connect-config-user").value = String(data.service_user || "");
    q("spotify-connect-config-scope").value = String(data.service_scope || "auto");
    q("spotify-connect-config-candidates").value = String(data.service_candidates || "");
    q("spotify-connect-config-device-name").value = String(data.device_name || "");
    return payload;
  }

  async function saveSpotifyConnectConfig() {
    const service_name = String(q("spotify-connect-config-name")?.value || "").trim();
    const service_user = String(q("spotify-connect-config-user")?.value || "").trim();
    const service_scope = String(q("spotify-connect-config-scope")?.value || "auto").trim();
    const service_candidates = String(q("spotify-connect-config-candidates")?.value || "").trim();
    const device_name = String(q("spotify-connect-config-device-name")?.value || "").trim();
    await fetchJson("/api/spotify-connect/config", {
      method: "POST",
      body: { service_name, service_user, service_scope, service_candidates, device_name },
      timeoutMs: 12000,
    });
    toast("Spotify Connect Konfiguration gespeichert", "success");
    await refreshSpotifyConnectStatus();
  }

  async function spotifyConnectAction(action) {
    const safeAction = String(action || "").trim().toLowerCase();
    if (!["start", "stop", "restart", "refresh", "enable", "disable", "install"].includes(safeAction)) {
      throw new Error("Ungültige Spotify-Action.");
    }
    const payload = await fetchJson(`/api/spotify-connect/${safeAction}`, { method: "POST" });
    renderSpotifyConnectStatus(payload);
    toast(`Spotify Connect ${safeAction}`, "success");
  }

  function renderAudioHubStatus(payload) {
    const data = (payload && payload.data && typeof payload.data === "object")
      ? payload.data
      : ((payload && typeof payload === "object") ? payload : {});
    const activeSource = String(data.active_source || "idle");
    const activeSourceDetail = (data.active_source_detail && typeof data.active_source_detail === "object")
      ? data.active_source_detail
      : {};
    const state = (data.state && typeof data.state === "object") ? data.state : {};
    const stateSourcePayload = (state.active_source_payload && typeof state.active_source_payload === "object")
      ? state.active_source_payload
      : {};
    const bluetooth = (data.bluetooth && typeof data.bluetooth === "object") ? data.bluetooth : {};
    const outputs = (data.outputs && typeof data.outputs === "object") ? data.outputs : {};
    const savedOutput = String(((outputs.saved || {}).selected_output) || "").trim();
    const effectiveOutput = String(outputs.current_output || "").trim() || savedOutput;
    const errors = Array.isArray(data.errors) ? data.errors : [];

    let sourceLabel = activeSource || "-";
    if (activeSource === "tts") {
      const filePath = String(activeSourceDetail.file_path || "").trim();
      sourceLabel = filePath ? `tts (${filePath})` : "tts";
    } else if (activeSource === "radio") {
      const radio = (data.radio && typeof data.radio === "object") ? data.radio : {};
      const streamUrl = String(
        activeSourceDetail.stream_url
        || activeSourceDetail.playback_url
        || stateSourcePayload.stream_url
        || stateSourcePayload.streamUrl
        || stateSourcePayload.playback_url
        || stateSourcePayload.url
        || radio.stream_url
        || radio.playback_url
        || ""
      ).trim();
      sourceLabel = streamUrl ? `radio (${streamUrl})` : "radio";
    } else {
      const runtimeSource = String(
        activeSourceDetail.source
        || stateSourcePayload.source
        || stateSourcePayload.url
        || ""
      ).trim();
      if (runtimeSource) {
        sourceLabel = `${activeSource} (${runtimeSource})`;
      }
    }
    q("audio-status-source").textContent = sourceLabel;
    if (bluetooth.ok) {
      q("audio-status-bluetooth").textContent = `verbunden: ${Number(bluetooth.connected_count || 0)}`;
    } else {
      const btError = String(bluetooth.error || "").trim();
      q("audio-status-bluetooth").textContent = btError ? `Fehler: ${btError}` : "nicht verfuegbar";
    }
    if (outputs.ok) {
      q("audio-status-output").textContent = effectiveOutput || "-";
    } else {
      q("audio-status-output").textContent = "nicht verfuegbar";
    }
    q("audio-status-updated").textContent = String(data.updated_at || "-");

    const errorEl = q("audio-status-errors");
    if (errorEl) {
      if (errors.length === 0) {
        errorEl.textContent = "Keine Fehler erkannt.";
      } else {
        errorEl.textContent = errors.map((item) => `${item.scope || "system"}: ${item.message || ""}`.trim()).join(" | ");
      }
    }

    const radio = (data.radio && typeof data.radio === "object") ? data.radio : {};
    const rtspAdapter = (radio.rtsp_adapter && typeof radio.rtsp_adapter === "object") ? radio.rtsp_adapter : {};
    const adapterMeta = rtspAdapter.active
      ? ` | RTSP-Adapter aktiv (${rtspAdapter.target_url || "-"})`
      : "";
    const radioStatus = radio.running
      ? `laeuft (${radio.stream_url || "-"})${adapterMeta}`
      : "inaktiv";
    q("audio-radio-status").textContent = `Status: ${radioStatus}`;

    const tts = (data.tts && typeof data.tts === "object") ? data.tts : {};
    const ttsStatus = tts.running ? "laeuft" : "inaktiv";
    q("audio-tts-status").textContent = `Status: ${ttsStatus}`;
  }

  async function refreshAudioHubStatus() {
    const warmupData = consumeWarmupData(["sections", "legacy", "audio_status"]);
    if (warmupData && typeof warmupData === "object") {
      renderAudioHubStatus(warmupData);
      return warmupData;
    }
    const payload = await fetchJson("/api/audio/status", { cache: "no-store" });
    renderAudioHubStatus(payload);
    return payload;
  }

  async function primeAudioHubData() {
    await Promise.allSettled([
      refreshAudioOutputs(),
      refreshAudioMixer(),
      refreshAudioHubStatus(),
    ]);
  }

  function setAudioRangeLabel(rangeId, labelId) {
    const raw = Number(q(rangeId)?.value || 0);
    const safe = Number.isFinite(raw) ? Math.max(0, Math.round(raw)) : 0;
    const el = q(labelId);
    if (el) el.textContent = String(safe);
  }

  function updateAudioTtsTargetUi() {
    const mode = String(q("audio-tts-target-mode")?.value || "current").trim().toLowerCase();
    const targetSelect = q("audio-tts-target-output");
    if (!targetSelect) return;
    targetSelect.disabled = mode !== "specific";
  }

  function currentSinkNameFromMixer() {
    const root = (audioMixerState.lastPayload && typeof audioMixerState.lastPayload === "object") ? audioMixerState.lastPayload : {};
    const outputs = (root.outputs && typeof root.outputs === "object") ? root.outputs : {};
    const currentOutput = String(outputs.current_output || "").trim();
    const available = Array.isArray(outputs.available_outputs) ? outputs.available_outputs : [];
    for (const item of available) {
      if (!item || typeof item !== "object") continue;
      if (String(item.id || "").trim() !== currentOutput) continue;
      const sinkName = String(item.sink_name || "").trim();
      if (sinkName) return sinkName;
    }
    return "";
  }

  function currentOutputIdFromMixer() {
    const root = (audioMixerState.lastPayload && typeof audioMixerState.lastPayload === "object") ? audioMixerState.lastPayload : {};
    const outputs = (root.outputs && typeof root.outputs === "object") ? root.outputs : {};
    return String(outputs.current_output || "").trim();
  }

  function syncMasterAndCurrentChannelUi(volumePercent) {
    const volume = Math.max(0, Math.min(100, Math.round(Number(volumePercent) || 0)));
    const currentOutputId = currentOutputIdFromMixer();
    if (!currentOutputId) return;
    const sliders = document.querySelectorAll("#audio-channel-list input[type='range'][data-channel-id]");
    for (const slider of sliders) {
      if (!slider || typeof slider !== "object") continue;
      if (String(slider.dataset.channelId || "").trim() !== currentOutputId) continue;
      slider.value = String(volume);
      const wrap = slider.closest(".border.rounded.p-2");
      const valueEl = wrap ? wrap.querySelector("[data-role='audio-channel-value']") : null;
      if (valueEl) valueEl.textContent = String(volume);
      break;
    }
  }

  function renderAudioMixer(payload) {
    const root = (payload && payload.data && typeof payload.data === "object")
      ? payload.data
      : ((payload && typeof payload === "object") ? payload : {});
    audioMixerState.lastPayload = root;
    const outputs = (root.outputs && typeof root.outputs === "object") ? root.outputs : {};
    const sources = (root.sources && typeof root.sources === "object") ? root.sources : {};
    const settings = (root.settings && typeof root.settings === "object") ? root.settings : {};
    const channelVolumes = (settings.channel_volumes && typeof settings.channel_volumes === "object")
      ? settings.channel_volumes
      : {};
    const micVolumes = (settings.mic_volumes && typeof settings.mic_volumes === "object")
      ? settings.mic_volumes
      : {};

    const ttsVolume = Number(settings.tts_volume_percent);
    const ttsTargetMode = String(settings.tts_target_mode || "current").trim().toLowerCase();
    const ttsTargetOutputId = String(settings.tts_target_output_id || "").trim();
    const duckingLevel = Number(settings.ducking_level_percent);
    const duckingEnabled = !!settings.ducking_enabled;
    const duckingAttack = Number(settings.ducking_attack_ms);
    const duckingRelease = Number(settings.ducking_release_ms);

    if (q("audio-tts-volume")) q("audio-tts-volume").value = String(Number.isFinite(ttsVolume) ? Math.max(0, Math.min(150, Math.round(ttsVolume))) : 90);
    if (q("audio-tts-target-mode")) q("audio-tts-target-mode").value = ["current", "specific", "all"].includes(ttsTargetMode) ? ttsTargetMode : "current";
    if (q("audio-ducking-level")) q("audio-ducking-level").value = String(Number.isFinite(duckingLevel) ? Math.max(0, Math.min(100, Math.round(duckingLevel))) : 30);
    if (q("audio-ducking-enabled")) q("audio-ducking-enabled").value = duckingEnabled ? "1" : "0";
    if (q("audio-ducking-attack")) q("audio-ducking-attack").value = String(Number.isFinite(duckingAttack) ? Math.max(0, Math.min(10000, Math.round(duckingAttack))) : 120);
    if (q("audio-ducking-release")) q("audio-ducking-release").value = String(Number.isFinite(duckingRelease) ? Math.max(0, Math.min(30000, Math.round(duckingRelease))) : 450);
    setAudioRangeLabel("audio-tts-volume", "audio-tts-volume-value");
    setAudioRangeLabel("audio-ducking-level", "audio-ducking-level-value");
    const ttsTargetSelect = q("audio-tts-target-output");
    if (ttsTargetSelect) {
      ttsTargetSelect.innerHTML = "";
      const availableOutputs = Array.isArray(outputs.available_outputs) ? outputs.available_outputs : [];
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "Bitte wählen…";
      ttsTargetSelect.appendChild(placeholder);
      for (const item of availableOutputs) {
        if (!item || typeof item !== "object") continue;
        const id = String(item.id || "").trim();
        if (!id) continue;
        const label = String(item.label || id);
        const opt = document.createElement("option");
        opt.value = id;
        const isAvailable = !!item.available;
        opt.textContent = isAvailable ? label : `${label} (derzeit nicht verfügbar)`;
        if (id === ttsTargetOutputId) {
          opt.selected = true;
        }
        ttsTargetSelect.appendChild(opt);
      }
      if (ttsTargetOutputId && !Array.from(ttsTargetSelect.options).some((opt) => opt.value === ttsTargetOutputId)) {
        const customOpt = document.createElement("option");
        customOpt.value = ttsTargetOutputId;
        customOpt.textContent = `${ttsTargetOutputId} (nicht verfügbar)`;
        customOpt.selected = true;
        ttsTargetSelect.appendChild(customOpt);
      }
    }
    updateAudioTtsTargetUi();

    const masterSlider = q("stream-audio-volume");
    const masterValueEl = q("stream-audio-volume-value");
    const currentOutput = String(outputs.current_output || "").trim();
    const availableOutputs = Array.isArray(outputs.available_outputs) ? outputs.available_outputs : [];
    let currentOutputVolume = null;
    for (const item of availableOutputs) {
      if (!item || typeof item !== "object") continue;
      if (String(item.id || "").trim() !== currentOutput) continue;
      const v = Number(item.volume_percent);
      if (Number.isFinite(v)) {
        currentOutputVolume = Math.max(0, Math.min(100, Math.round(v)));
      }
      break;
    }
    const configuredMaster = Number(settings.master_volume_percent);
    const masterVolume = Number.isFinite(currentOutputVolume)
      ? currentOutputVolume
      : (Number.isFinite(configuredMaster) ? Math.max(0, Math.min(100, Math.round(configuredMaster))) : 65);
    if (masterSlider) masterSlider.value = String(masterVolume);
    if (masterValueEl) masterValueEl.textContent = String(masterVolume);
    streamAudioMuted = masterVolume === 0;
    if (masterVolume > 0) streamAudioLastNonZeroVolume = masterVolume;
    const muteBtn = q("btn-stream-audio-mute");
    if (muteBtn) muteBtn.textContent = streamAudioMuted ? "Ton an" : "Stumm";

    const channelHost = q("audio-channel-list");
    if (channelHost) {
      channelHost.innerHTML = "";
      const available = Array.isArray(outputs.available_outputs) ? outputs.available_outputs : [];
      if (!available.length) {
        channelHost.innerHTML = '<div class="small text-secondary">Keine Audio-Kanäle erkannt.</div>';
      } else {
        for (const item of available) {
          if (!item || typeof item !== "object") continue;
          const channelId = String(item.id || "").trim();
          if (!channelId) continue;
          const label = String(item.label || channelId);
          const sinkName = String(item.sink_name || "");
          const availableNow = !!item.available;
          const volRaw = Number(item.volume_percent);
          const fallbackRaw = Number(channelVolumes[channelId]);
          const volume = Number.isFinite(volRaw)
            ? Math.max(0, Math.min(100, Math.round(volRaw)))
            : (Number.isFinite(fallbackRaw) ? Math.max(0, Math.min(100, Math.round(fallbackRaw))) : 65);

          const wrap = document.createElement("div");
          wrap.className = "border rounded p-2";
          wrap.innerHTML = `
            <div class="d-flex flex-wrap justify-content-between align-items-center gap-2 mb-2">
              <div>
                <div class="fw-semibold">${escapeHtml(label)}</div>
                <div class="small text-secondary">${escapeHtml(channelId)}${sinkName ? ` · ${escapeHtml(sinkName)}` : ""}</div>
              </div>
              <div class="d-flex align-items-center gap-2">
                <span class="badge text-bg-${availableNow ? "success" : "secondary"}">${availableNow ? "verfügbar" : "nicht verfügbar"}</span>
                <span class="badge text-bg-light border"><span data-role="audio-channel-value">${volume}</span>%</span>
              </div>
            </div>
          `;
          const slider = document.createElement("input");
          slider.type = "range";
          slider.min = "0";
          slider.max = "100";
          slider.step = "1";
          slider.value = String(volume);
          slider.className = "form-range mb-0";
          slider.disabled = !availableNow;
          slider.dataset.channelId = channelId;
          slider.addEventListener("input", () => {
            const valueEl = wrap.querySelector('[data-role="audio-channel-value"]');
            const current = Math.max(0, Math.min(100, Math.round(Number(slider.value) || 0)));
            if (valueEl) valueEl.textContent = String(current);
            if (channelId === currentOutput) {
              const masterSlider = q("stream-audio-volume");
              const masterValueEl = q("stream-audio-volume-value");
              if (masterSlider) masterSlider.value = String(current);
              if (masterValueEl) masterValueEl.textContent = String(current);
            }
            queueAudioChannelVolume(channelId, Number(slider.value || 0));
          });
          slider.addEventListener("change", () => {
            run(() => setAudioChannelVolume(channelId, Number(slider.value || 0), { notify: false, refresh: false }));
          });
          wrap.appendChild(slider);
          channelHost.appendChild(wrap);
        }
      }
    }

    const micHost = q("audio-mic-list");
    if (micHost) {
      micHost.innerHTML = "";
      const microphones = Array.isArray(sources.microphones)
        ? sources.microphones
        : (Array.isArray(sources.sources) ? sources.sources.filter((item) => !!item && !!item.is_microphone) : []);
      if (!microphones.length) {
        micHost.innerHTML = '<div class="small text-secondary">Keine Mikrofone erkannt.</div>';
      } else {
        for (const mic of microphones) {
          if (!mic || typeof mic !== "object") continue;
          const sourceName = String(mic.name || "").trim();
          if (!sourceName) continue;
          const label = String(mic.description || sourceName);
          const isDefault = !!mic.is_default;
          const rawVolume = Number(mic.volume_percent);
          const fallbackVolume = Number(micVolumes[sourceName]);
          const volume = Number.isFinite(rawVolume)
            ? Math.max(0, Math.min(150, Math.round(rawVolume)))
            : (Number.isFinite(fallbackVolume) ? Math.max(0, Math.min(150, Math.round(fallbackVolume))) : 70);

          const wrap = document.createElement("div");
          wrap.className = "border rounded p-2";
          wrap.innerHTML = `
            <div class="d-flex flex-wrap justify-content-between align-items-center gap-2 mb-2">
              <div>
                <div class="fw-semibold">${escapeHtml(label)}</div>
                <div class="small text-secondary">${escapeHtml(sourceName)}</div>
              </div>
              <div class="d-flex align-items-center gap-2">
                ${isDefault ? '<span class="badge text-bg-primary">default</span>' : ""}
                <span class="badge text-bg-light border"><span data-role="audio-mic-value">${volume}</span>%</span>
              </div>
            </div>
          `;
          const slider = document.createElement("input");
          slider.type = "range";
          slider.min = "0";
          slider.max = "150";
          slider.step = "1";
          slider.value = String(volume);
          slider.className = "form-range mb-0";
          slider.dataset.sourceName = sourceName;
          slider.addEventListener("input", () => {
            const valueEl = wrap.querySelector('[data-role="audio-mic-value"]');
            if (valueEl) valueEl.textContent = String(Math.max(0, Math.min(150, Math.round(Number(slider.value) || 0))));
            queueAudioMicVolume(sourceName, Number(slider.value || 0));
          });
          slider.addEventListener("change", () => {
            run(() => setAudioMicVolume(sourceName, Number(slider.value || 0), { notify: false, refresh: false }));
          });
          wrap.appendChild(slider);
          micHost.appendChild(wrap);
        }
      }
    }
  }

  async function refreshAudioMixer() {
    const warmupData = consumeWarmupData(["sections", "legacy", "audio_mixer"]);
    if (warmupData && typeof warmupData === "object") {
      renderAudioMixer(warmupData);
      return warmupData;
    }
    const payload = await fetchJson("/api/audio/mixer", { cache: "no-store" });
    renderAudioMixer(payload);
    return payload;
  }

  async function setAudioChannelVolume(channelId, volumePercent, options = {}) {
    const id = String(channelId || "").trim();
    if (!id) throw new Error("Kanal fehlt.");
    const volume = Math.max(0, Math.min(100, Math.round(Number(volumePercent) || 0)));
    await fetchJson("/api/audio/channel/volume", {
      method: "POST",
      body: { channel_id: id, volume_percent: volume },
      timeoutMs: 12000,
    });
    if (options.refresh) {
      await refreshAudioMixer();
    }
    if (options.notify) {
      toast(`Kanal ${id}: ${volume}%`, "success");
    }
  }

  function queueAudioChannelVolume(channelId, volumePercent) {
    const id = String(channelId || "").trim();
    if (!id) return;
    const current = audioMixerState.channelDebounce.get(id);
    if (current) {
      window.clearTimeout(current);
    }
    const handle = window.setTimeout(() => {
      audioMixerState.channelDebounce.delete(id);
      setAudioChannelVolume(id, volumePercent, { notify: false, refresh: false }).catch(() => {});
    }, 140);
    audioMixerState.channelDebounce.set(id, handle);
  }

  async function setAudioMicVolume(sourceName, volumePercent, options = {}) {
    const name = String(sourceName || "").trim();
    if (!name) throw new Error("Mikrofon fehlt.");
    const volume = Math.max(0, Math.min(150, Math.round(Number(volumePercent) || 0)));
    await fetchJson("/api/audio/mic/volume", {
      method: "POST",
      body: { source_name: name, volume_percent: volume },
      timeoutMs: 12000,
    });
    if (options.refresh) {
      await refreshAudioMixer();
    }
    if (options.notify) {
      toast(`Mikrofon ${name}: ${volume}%`, "success");
    }
  }

  function queueAudioMicVolume(sourceName, volumePercent) {
    const name = String(sourceName || "").trim();
    if (!name) return;
    const current = audioMixerState.micDebounce.get(name);
    if (current) {
      window.clearTimeout(current);
    }
    const handle = window.setTimeout(() => {
      audioMixerState.micDebounce.delete(name);
      setAudioMicVolume(name, volumePercent, { notify: false, refresh: false }).catch(() => {});
    }, 140);
    audioMixerState.micDebounce.set(name, handle);
  }

  async function saveAudioMixerSettings() {
    const ttsVolume = Math.max(0, Math.min(150, Math.round(Number(q("audio-tts-volume")?.value || 90))));
    const ttsTargetModeRaw = String(q("audio-tts-target-mode")?.value || "current").trim().toLowerCase();
    const ttsTargetMode = ["current", "specific", "all"].includes(ttsTargetModeRaw) ? ttsTargetModeRaw : "current";
    const ttsTargetOutputId = String(q("audio-tts-target-output")?.value || "").trim();
    const duckingLevel = Math.max(0, Math.min(100, Math.round(Number(q("audio-ducking-level")?.value || 30))));
    const duckingEnabled = String(q("audio-ducking-enabled")?.value || "1") === "1";
    const duckingAttack = Math.max(0, Math.min(10000, Math.round(Number(q("audio-ducking-attack")?.value || 120))));
    const duckingRelease = Math.max(0, Math.min(30000, Math.round(Number(q("audio-ducking-release")?.value || 450))));

    await fetchJson("/api/audio/mixer/settings", {
      method: "POST",
      body: {
        tts_volume_percent: ttsVolume,
        tts_target_mode: ttsTargetMode,
        tts_target_output_id: ttsTargetOutputId,
        ducking_enabled: duckingEnabled,
        ducking_level_percent: duckingLevel,
        ducking_attack_ms: duckingAttack,
        ducking_release_ms: duckingRelease,
      },
      timeoutMs: 12000,
    });
    toast("Audio Ducking/TTS gespeichert", "success");
    await refreshAudioMixer();
  }

  async function audioRadioPlay() {
    const url = String(q("audio-radio-url")?.value || "").trim();
    if (!url) throw new Error("Bitte Webradio-URL eingeben.");
    const selectedOutput = String(q("bt-audio-output-select")?.value || "").trim()
      || String((btDeviceState.audioOutputs && btDeviceState.audioOutputs.current_output) || "").trim()
      || String(((btDeviceState.audioOutputs || {}).saved || {}).selected_output || "").trim();
    const btn = q("btn-audio-radio-play");
    const original = btn ? btn.innerHTML : "";
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Start...';
    }
    try {
      await fetchJson("/api/audio/radio/play", {
        method: "POST",
        body: selectedOutput ? { stream_url: url, output: selectedOutput } : { stream_url: url },
        timeoutMs: 12000,
      });
      await refreshAudioHubStatus();
      toast("Radio gestartet", "success");
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = original;
      }
    }
  }

  async function audioRadioStop() {
    const btn = q("btn-audio-radio-stop");
    const original = btn ? btn.innerHTML : "";
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Stop...';
    }
    try {
      await fetchJson("/api/audio/radio/stop", { method: "POST", timeoutMs: 8000 });
      await refreshAudioHubStatus();
      toast("Radio gestoppt", "success");
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = original;
      }
    }
  }

  async function audioTtsTest() {
    const text = String(q("audio-tts-text")?.value || "").trim();
    const btn = q("btn-audio-tts-test");
    const original = btn ? btn.innerHTML : "";
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Sprechen...';
    }
    try {
      await fetchJson("/api/audio/tts/test", { method: "POST", body: { text }, timeoutMs: 20000 });
      await refreshAudioHubStatus();
      toast("TTS gestartet", "success");
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = original;
      }
    }
  }

  function startAudioHubPolling() {
    if (audioHubPollHandle) return;
    audioHubPollHandle = window.setInterval(() => {
      refreshAudioHubStatus().catch(() => {});
    }, 12000);
  }

  function renderStreamAudioStatus(payload) {
    const data = (payload && payload.data && typeof payload.data === "object") ? payload.data : {};
    const health = (data.health && typeof data.health === "object") ? data.health : {};
    const state = String(data.state || "-");
    const sourceType = String(data.source_type || data.hub_active_source || "-");
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

  async function refreshStreamAudioFiles(pathOverride = "") {
    const rawInput = String(q("stream-audio-file-path")?.value || "").trim();
    const requestedPath = String(pathOverride || rawInput || "").trim();
    const query = new URLSearchParams();
    if (requestedPath && !/^https?:\/\//i.test(requestedPath) && !/\.[A-Za-z0-9]{2,6}$/.test(requestedPath)) {
      query.set("path", requestedPath);
    }
    const suffix = query.toString();
    const payload = await fetchJson(`/api/stream/player/audio/files${suffix ? `?${suffix}` : ""}`, { cache: "no-store" });
    const files = Array.isArray(payload.files) ? payload.files : [];
    const currentPath = String(payload.current_path || payload.root || "-");
    q("stream-audio-root").textContent = currentPath;
    const select = q("stream-audio-file-select");
    let firstPath = files.length > 0 ? String(files[0].path || "") : "";
    const currentInput = String(q("stream-audio-file-path")?.value || "").trim();
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
          if (currentInput && currentInput === String(file.path || "")) {
            opt.selected = true;
            firstPath = currentInput;
          }
          select.appendChild(opt);
        }
      }
    }
    if (!currentInput || currentInput === currentPath || currentInput.endsWith("/")) {
      q("stream-audio-file-path").value = firstPath || currentPath;
    }
    return payload;
  }

  function renderStreamAudioPathBrowser(data = {}) {
    streamAudioBrowserState.currentPath = String(data.current_path || "");
    streamAudioBrowserState.parentPath = String(data.parent_path || "");
    streamAudioBrowserState.rootPath = String(data.root_path || "/mnt");

    const currentEl = q("stream-audio-browser-current");
    if (currentEl) currentEl.textContent = streamAudioBrowserState.currentPath || "-";

    const listEl = q("stream-audio-browser-list");
    if (!listEl) return;

    const rows = [];
    if (streamAudioBrowserState.currentPath && streamAudioBrowserState.currentPath !== streamAudioBrowserState.rootPath) {
      rows.push(`<button type="button" class="list-group-item list-group-item-action js-stream-audio-path-entry" data-kind="dir" data-path="${escapeHtml(streamAudioBrowserState.parentPath)}"><i class="bi bi-arrow-up-circle me-1"></i>..</button>`);
    }

    const directories = Array.isArray(data.directories) ? data.directories : [];
    for (const dir of directories) {
      const name = escapeHtml(String(dir.name || ""));
      const path = escapeHtml(String(dir.path || ""));
      rows.push(`<button type="button" class="list-group-item list-group-item-action js-stream-audio-path-entry" data-kind="dir" data-path="${path}"><i class="bi bi-folder2 me-1"></i>${name}</button>`);
    }

    const files = Array.isArray(data.files) ? data.files : [];
    for (const file of files) {
      const sizeKb = Math.max(0, Math.round(Number(file.size_bytes || 0) / 1024));
      const name = escapeHtml(String(file.name || ""));
      const path = escapeHtml(String(file.path || ""));
      rows.push(`<button type="button" class="list-group-item list-group-item-action js-stream-audio-path-entry" data-kind="file" data-path="${path}"><i class="bi bi-file-earmark-music me-1"></i>${name} <span class="text-secondary">(${sizeKb} KB)</span></button>`);
    }

    listEl.innerHTML = rows.length ? rows.join("") : '<div class="text-secondary p-2">Keine Unterordner oder Audiodateien.</div>';
  }

  async function refreshStreamAudioPathBrowser(path = "") {
    const query = new URLSearchParams();
    const targetPath = String(path || streamAudioBrowserState.currentPath || "").trim();
    if (targetPath) query.set("path", targetPath);
    const payload = await fetchJson(`/api/stream/player/audio/path-browser?${query.toString()}`, { timeoutMs: 10000 });
    renderStreamAudioPathBrowser((payload || {}).data || {});
  }

  async function openStreamAudioPathModal() {
    const inputPath = String(q("stream-audio-file-path")?.value || "").trim();
    await refreshStreamAudioPathBrowser(inputPath);
    const modalEl = q("streamAudioFileBrowserModal");
    if (!modalEl || !window.bootstrap || !window.bootstrap.Modal) return;
    streamAudioBrowserState.modal = window.bootstrap.Modal.getOrCreateInstance(modalEl);
    streamAudioBrowserState.modal.show();
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
    const currentOutputId = currentOutputIdFromMixer();
    const sinkName = currentSinkNameFromMixer();
    if (currentOutputId) {
      await fetchJson("/api/audio/channel/volume", {
        method: "POST",
        body: {
          channel_id: currentOutputId,
          volume_percent: clamped,
        },
        timeoutMs: 12000,
      });
    } else {
      await fetchJson("/api/audio/volume", {
        method: "POST",
        body: {
          volume_percent: clamped,
          sink_name: sinkName || "",
        },
        timeoutMs: 12000,
      });
    }
    const valueEl = q("stream-audio-volume-value");
    if (valueEl) valueEl.textContent = String(clamped);
    syncMasterAndCurrentChannelUi(clamped);
    if (clamped > 0) {
      streamAudioLastNonZeroVolume = clamped;
    }
    streamAudioMuted = clamped === 0;
    const muteBtn = q("btn-stream-audio-mute");
    if (muteBtn) {
      muteBtn.textContent = streamAudioMuted ? "Ton an" : "Stumm";
    }
    if (refresh) {
      await refreshAudioMixer();
      await refreshAudioHubStatus();
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
    const repoValue = String(cfg.player_repo_link || cfg.player_repo_dir || "");
    const serviceNameValue = String(cfg.player_service_name || DEFAULT_PLAYER_SERVICE_NAME);
    const serviceUserValue = String(cfg.player_service_user || "");
    const repoMain = q("stream-player-repo-dir");
    const nameMain = q("stream-player-service-name");
    const userMain = q("stream-player-service-user");
    if (repoMain) repoMain.value = repoValue;
    if (nameMain) nameMain.value = serviceNameValue;
    if (userMain) userMain.value = serviceUserValue;
    const repoQuick = q("stream-player-repo-dir-quick");
    const nameQuick = q("stream-player-service-name-quick");
    const userQuick = q("stream-player-service-user-quick");
    if (repoQuick) repoQuick.value = repoValue;
    if (nameQuick) nameQuick.value = serviceNameValue;
    if (userQuick) userQuick.value = serviceUserValue;
    await refreshSystemUpdateSummaries();
  }

  function renderSystemUpdateSummary(summary = {}) {
    currentSystemUpdateSummary = summary && typeof summary === "object" ? summary : null;
    const portal = (summary.portal && typeof summary.portal === "object") ? summary.portal : {};
    const player = (summary.player && typeof summary.player === "object") ? summary.player : {};
    const portalStatus = (portal.status && typeof portal.status === "object") ? portal.status : {};
    const playerStatus = (player.status && typeof player.status === "object") ? player.status : {};
    const portalUpdate = resolveRepoUpdateInfo({
      repo_link: portal.repo,
      install_dir: portal.install_dir,
      service_name: portal.service_name,
    });
    const playerUpdate = resolveRepoUpdateInfo({
      repo_link: player.repo,
      install_dir: player.install_dir,
      service_name: player.service_name,
    });

    renderServiceBadges("system-portal-status-badges", portalStatus, true, true, true, portalUpdate);
    renderServiceBadges("system-player-status-badges", playerStatus, true, true, true, playerUpdate);
    const portalCheckWrap = q("system-portal-update-check-wrap");
    const playerCheckWrap = q("system-player-update-check-wrap");
    if (portalCheckWrap) {
      const hasUpdate = !!(portalUpdate && portalUpdate.available);
      portalCheckWrap.classList.toggle("d-none", !hasUpdate);
      const cb = portalCheckWrap.querySelector(".js-repo-update-check");
      if (cb && !hasUpdate) cb.checked = false;
    }
    if (playerCheckWrap) {
      const hasUpdate = !!(playerUpdate && playerUpdate.available);
      playerCheckWrap.classList.toggle("d-none", !hasUpdate);
      const cb = playerCheckWrap.querySelector(".js-repo-update-check");
      if (cb && !hasUpdate) cb.checked = false;
    }
    syncSystemRepoUpdateButtons(portalUpdate, playerUpdate);
    syncSystemUpdateToggleButtons(summary);
    renderHeroUpdateBadge((statusDashboardState.status || {}).app_update || {}, repoUpdatesState);
  }

  function isServiceActive(status = {}) {
    if (!status || typeof status !== "object") return false;
    if (status.active === true || status.service_running === true) return true;
    const activeState = String(status.active_state || "").trim().toLowerCase();
    return activeState === "active" || activeState === "activating";
  }

  function syncToggleButtonState(buttonId, running) {
    const btn = q(buttonId);
    if (!btn) return;
    btn.dataset.action = running ? "stop" : "start";
    btn.textContent = running ? "Stop" : "Start";
    btn.classList.remove("btn-outline-success", "btn-outline-warning");
    btn.classList.add(running ? "btn-outline-warning" : "btn-outline-success");
  }

  function syncSystemUpdateToggleButtons(summary = {}) {
    const portal = (summary.portal && typeof summary.portal === "object") ? summary.portal : {};
    const player = (summary.player && typeof summary.player === "object") ? summary.player : {};
    const portalStatus = (portal.status && typeof portal.status === "object") ? portal.status : {};
    const playerStatus = (player.status && typeof player.status === "object") ? player.status : {};
    syncToggleButtonState("btn-system-portal-toggle", isServiceActive(portalStatus));
    syncToggleButtonState("btn-system-player-toggle", isServiceActive(playerStatus));
  }

  function buildServiceBadgesHtml(status = {}, configAutostart = null, includeServiceBadge = false, serviceEnabled = true, updateInfo = null) {
    const installed = !!status.service_installed;
    const running = !!status.active || String(status.active_state || "").toLowerCase() === "activating";
    const autostartInstalled = installed;
    const autostartActive = autostartInstalled ? !!status.service_enabled : false;
    const badges = [
      `<span class="badge text-bg-light border">Installiert: ${installed ? "ja" : "nein"}</span>`,
      `<span class="badge text-bg-light border">Läuft: ${running ? "ja" : "nein"}</span>`,
      `<span class="badge text-bg-light border">Autostart installiert: ${autostartInstalled ? "ja" : "nein"}</span>`,
      `<span class="badge text-bg-light border">Autostart aktiv: ${autostartInstalled ? (autostartActive ? "ja" : "nein") : "-"}</span>`,
    ];
    if (includeServiceBadge) {
      badges.unshift(`<span class="badge text-bg-light border">Service: ${serviceEnabled ? "ja" : "nein"}</span>`);
    }
    if (updateInfo && typeof updateInfo === "object") {
      if (updateInfo.available) {
        badges.push('<span class="badge text-bg-warning border">Update: verfügbar</span>');
      } else if (String(updateInfo.error || "").trim()) {
        badges.push('<span class="badge text-bg-secondary border">Update: unbekannt</span>');
      } else {
        badges.push('<span class="badge text-bg-success border">Update: aktuell</span>');
      }
    }
    return badges.join("");
  }

  function renderServiceBadges(hostId, status = {}, configAutostart = null, includeServiceBadge = false, serviceEnabled = true, updateInfo = null) {
    const host = q(hostId);
    if (!host) return;
    host.innerHTML = buildServiceBadgesHtml(status, configAutostart, includeServiceBadge, serviceEnabled, updateInfo);
  }

  function findLlmManagerRepo(repos = []) {
    const list = Array.isArray(repos) ? repos : [];
    return list.find((item) => {
      const name = String(item?.name || "").toLowerCase();
      const link = String(item?.repo_link || item?.repo_dir || "").toLowerCase();
      const service = String(item?.service_name || "").toLowerCase();
      return name.includes("llm-lab") || link.includes("jarvis-llm-lab") || service.includes("llm-lab");
    }) || null;
  }

  function renderLlmManagerModels(info) {
    const host = q("llm-manager-models");
    if (!host) return;
    const models = Array.isArray(info?.models) ? info.models : [];
    if (!models.length) {
      host.classList.add("text-secondary");
      host.textContent = "Keine Modelle gemeldet.";
      return;
    }
    const defaultModel = String(info?.default_model || "").trim();
    const rows = models.map((m) => {
      const name = String(m?.name || m?.model || "-");
      const digest = String(m?.digest || "").trim();
      const shortDigest = digest ? digest.slice(0, 12) : "-";
      const modified = String(m?.modified_at || "-");
      const updateStatus = String(m?.update_status || "unknown");
      const tag = defaultModel && name === defaultModel ? " (default)" : "";
      return `<tr>
        <td class="text-break">${escapeHtml(name)}${escapeHtml(tag)}</td>
        <td class="text-break">${escapeHtml(shortDigest)}</td>
        <td>${escapeHtml(modified)}</td>
        <td>${escapeHtml(updateStatus)}</td>
      </tr>`;
    }).join("");
    host.classList.remove("text-secondary");
    host.innerHTML = `<div class="table-responsive"><table class="table table-sm align-middle mb-0">
      <thead><tr><th>Model</th><th>Version</th><th>Geändert</th><th>Update</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`;
  }

  async function refreshLlmManagerInfo() {
    const payload = await fetchJson("/api/llm-manager/status", { cache: "no-store" });
    const info = (payload && payload.data && typeof payload.data === "object")
      ? (payload.data.llm_manager || {})
      : {};
    llmManagerState.info = info;
    renderLlmManagerModels(info);
    const healthEl = q("llm-manager-health");
    if (healthEl) {
      const ollama = (info && typeof info === "object" && typeof info.ollama === "object") ? info.ollama : {};
      const ollamaOk = !!ollama.reachable;
      let label = ollamaOk ? "Health: ok" : "Health: nein";
      if (ollama && String(ollama.version || "").trim()) {
        label += ` · Ollama: ${String(ollama.version).trim()}`;
      }
      healthEl.textContent = label;
    }
    if (statusDashboardState.status) {
      renderLlmManagerCard(statusDashboardState.status, info);
    }
    return info;
  }

  function renderLlmManagerCard(statusPayload = {}, llmInfo = null) {
    const card = q("llm-manager-card");
    if (!card) return;
    const cfg = statusPayload && typeof statusPayload === "object" ? (statusPayload.config || {}) : {};
    const repos = (Array.isArray(managedInstallRepos) && managedInstallRepos.length)
      ? managedInstallRepos
      : (Array.isArray(cfg.managed_install_repos) ? cfg.managed_install_repos : []);
    const repo = findLlmManagerRepo(repos);
    if (!repo || typeof repo !== "object") {
      card.classList.add("d-none");
      return;
    }
    const status = (repo.service_status && typeof repo.service_status === "object") ? repo.service_status : {};
    const running = isServiceActive(status);
    const installed = !!(status.service_installed || running || _repoLooksInstalled(repo));
    if (!installed) {
      card.classList.add("d-none");
      return;
    }
    card.classList.remove("d-none");
    const updateInfo = resolveRepoUpdateInfo(repo);
    const info = (llmInfo && typeof llmInfo === "object") ? llmInfo : (llmManagerState.info || {});
    llmManagerState = { ...llmManagerState, info, repo, update: updateInfo };

    const badgesHost = q("llm-manager-badges");
    if (badgesHost) {
      const badgeStatus = { ...status, service_running: running };
      if (running) {
        badgeStatus.active_state = "active";
        badgeStatus.substate = "running";
      }
      badgesHost.innerHTML = buildServiceBadgesHtml(badgeStatus, repo.autostart !== false, true, true, updateInfo);
    }
    const healthEl = q("llm-manager-health");
    if (healthEl) {
      const ollama = (info && typeof info === "object" && typeof info.ollama === "object") ? info.ollama : {};
      const ollamaOk = !!ollama.reachable;
      const runtimeOk = !!status.runtime_reachable;
      let label = (ollamaOk || runtimeOk || running) ? "Health: ok" : "Health: nein";
      if (ollama && String(ollama.version || "").trim()) {
        label += ` · Ollama: ${String(ollama.version).trim()}`;
      }
      healthEl.textContent = label;
    }

    const updateBtn = q("btn-llm-manager-update");
    if (updateBtn) {
      updateBtn.classList.toggle("d-none", !(updateInfo && updateInfo.available));
    }

    const toggleBtn = q("btn-llm-manager-toggle");
    if (toggleBtn) {
      toggleBtn.dataset.action = running ? "stop" : "start";
      toggleBtn.textContent = running ? "Stop" : "Start";
      toggleBtn.classList.remove("btn-outline-success", "btn-outline-warning");
      toggleBtn.classList.add(running ? "btn-outline-warning" : "btn-outline-success");
    }

    const autostartBtn = q("btn-llm-manager-autostart");
    if (autostartBtn) {
      const enabled = !!status.service_enabled;
      autostartBtn.dataset.enabled = enabled ? "0" : "1";
      autostartBtn.textContent = enabled ? "Autostart deaktivieren" : "Autostart aktivieren";
    }

    renderLlmManagerModels(info || {});

    const models = Array.isArray(info?.models) ? info.models : [];
    if (!models.length && !llmManagerState.didAutoRefresh) {
      llmManagerState.didAutoRefresh = true;
      runQuiet(async () => {
        await fetchJson("/api/llm-manager/refresh", { method: "POST" });
        await refreshLlmManagerInfo();
        await refreshStatus();
      }, ["login_required", "llm_manager_unavailable"]);
    }
  }

  function syncSystemRepoUpdateButtons(portalUpdateInfo, playerUpdateInfo) {
    const portalBtn = q("btn-system-portal-update");
    if (portalBtn) {
      portalBtn.classList.toggle("d-none", !portalUpdateInfo || !portalUpdateInfo.available);
    }
    const playerBtn = q("btn-system-player-update");
    if (playerBtn) {
      playerBtn.classList.toggle("d-none", !playerUpdateInfo || !playerUpdateInfo.available);
    }
  }

  function showSystemUpdateDetails(kind) {
    const summary = currentSystemUpdateSummary && typeof currentSystemUpdateSummary === "object" ? currentSystemUpdateSummary : {};
    const key = String(kind || "").trim().toLowerCase() === "player" ? "player" : "portal";
    const entity = (summary[key] && typeof summary[key] === "object") ? summary[key] : {};
    const status = (entity.status && typeof entity.status === "object") ? entity.status : {};
    const configAutostart = typeof entity.autostart === "boolean" ? entity.autostart : true;

    const setText = (id, value) => {
      const el = q(id);
      if (el) el.textContent = String(value || "-");
    };
    setText(
      "system-update-details-title",
      key === "player" ? "Joormann-Media-Jarvis-DisplayPlayer Details" : "Device-Portal Details",
    );
    setText("system-update-details-repo", entity.repo || "-");
    setText("system-update-details-install-dir", entity.install_dir || "-");
    const fallbackServiceName = key === "player"
      ? String(q("stream-player-service-name")?.value || q("stream-player-service-name-quick")?.value || "").trim()
      : "device-portal.service";
    setText("system-update-details-service", entity.service_name || fallbackServiceName || "-");
    setText("system-update-details-user", entity.service_user || "-");

    const badgesHost = q("system-update-details-badges");
    if (badgesHost) {
      const updateInfo = resolveRepoUpdateInfo({
        repo_link: entity.repo,
        install_dir: entity.install_dir,
        service_name: entity.service_name,
      });
      badgesHost.innerHTML = buildServiceBadgesHtml(status, configAutostart, true, true, updateInfo);
    }
    const raw = q("system-update-details-json");
    if (raw) {
      raw.textContent = JSON.stringify(entity, null, 2);
    }
    const modalEl = q("systemUpdateDetailsModal");
    if (modalEl && window.bootstrap && window.bootstrap.Modal) {
      window.bootstrap.Modal.getOrCreateInstance(modalEl).show();
    }
  }

  async function refreshSystemUpdateSummaries() {
    const playerCfgResp = await fetchJson("/api/stream/player/repo", { timeoutMs: 10000 });
    const playerCfg = playerCfgResp.config || {};
    const playerServiceName = String(playerCfg.player_service_name || DEFAULT_PLAYER_SERVICE_NAME).trim() || DEFAULT_PLAYER_SERVICE_NAME;
    const portalStatusResp = await fetchJson("/api/system/service/status?service_name=device-portal.service", { timeoutMs: 10000 });
    const playerStatusResp = await fetchJson(`/api/system/service/status?service_name=${encodeURIComponent(playerServiceName)}`, { timeoutMs: 10000 });
    const portalSummaryResp = await fetchJson("/api/system/portal/summary", { timeoutMs: 10000 });
    const portalSummary = (portalSummaryResp && portalSummaryResp.data && typeof portalSummaryResp.data === "object") ? portalSummaryResp.data : {};

    const summary = {
      portal: {
        repo: String(portalSummary.repo_origin || portalSummary.repo_dir || "-"),
        install_dir: String(portalSummary.install_dir || portalSummary.repo_dir || "-"),
        service_name: "device-portal.service",
        service_user: String(portalSummary.service_user || "-"),
        status: (portalStatusResp && portalStatusResp.data) || {},
      },
      player: {
        repo: String(playerCfg.player_repo_link || playerCfg.player_repo_dir || "-"),
        install_dir: String(playerCfg.player_install_dir || "-"),
        service_name: playerServiceName,
        service_user: String(playerCfg.player_service_user || "-"),
        status: (playerStatusResp && playerStatusResp.data) || {},
      },
      updated_at: new Date().toISOString(),
    };
    renderSystemUpdateSummary(summary);
    try {
      window.localStorage.setItem(SYSTEM_UPDATE_SUMMARY_CACHE_KEY, JSON.stringify(summary));
    } catch (_) {}
    try {
      await fetchJson("/api/system/update-summary", { method: "POST", body: { summary }, timeoutMs: 8000 });
    } catch (_) {}
  }

  function bindMirroredInput(sourceId, targetId) {
    const source = q(sourceId);
    const target = q(targetId);
    if (!source || !target) return;
    const mirror = () => {
      if (target.value !== source.value) {
        target.value = source.value;
      }
    };
    source.addEventListener("input", mirror);
    source.addEventListener("change", mirror);
  }

  function initPlayerRepoFieldSync() {
    bindMirroredInput("stream-player-repo-dir", "stream-player-repo-dir-quick");
    bindMirroredInput("stream-player-repo-dir-quick", "stream-player-repo-dir");
    bindMirroredInput("stream-player-service-name", "stream-player-service-name-quick");
    bindMirroredInput("stream-player-service-name-quick", "stream-player-service-name");
    bindMirroredInput("stream-player-service-user", "stream-player-service-user-quick");
    bindMirroredInput("stream-player-service-user-quick", "stream-player-service-user");
  }

  function getPlayerFormValues() {
    const repoMain = String(q("stream-player-repo-dir")?.value || "").trim();
    const nameMain = String(q("stream-player-service-name")?.value || "").trim();
    const userMain = String(q("stream-player-service-user")?.value || "").trim();
    const repoQuick = String(q("stream-player-repo-dir-quick")?.value || "").trim();
    const nameQuick = String(q("stream-player-service-name-quick")?.value || "").trim();
    const userQuick = String(q("stream-player-service-user-quick")?.value || "").trim();
    return {
      player_repo_link: repoMain || repoQuick,
      player_service_name: (nameMain || nameQuick || DEFAULT_PLAYER_SERVICE_NAME).trim(),
      player_service_user: (userMain || userQuick || "").trim(),
    };
  }

  async function savePlayerRepoConfig() {
    const { player_repo_link, player_service_name, player_service_user } = getPlayerFormValues();
    await fetchJson("/api/stream/player/repo", {
      method: "POST",
      body: { player_repo_link, player_service_name, player_service_user },
      timeoutMs: 12000,
    });
    await loadPlayerRepoConfig();
    await refreshSystemUpdateSummaries();
    toast("Player-Repo Link gespeichert", "success");
  }

  function getManagedRepoFormValues() {
    const id = String(q("extra-repo-id")?.value || "").trim();
    const repoLink = String(q("extra-repo-link")?.value || "").trim();
    const serviceUser = String(q("extra-repo-service-user")?.value || "").trim();
    const installInput = String(q("extra-repo-install-dir")?.value || "").trim();
    const installDerived = deriveManagedRepoDefaultInstallDir(repoLink, serviceUser);
    const installDir = installInput || installDerived;
    return {
      id,
      name: String(q("extra-repo-name")?.value || "").trim(),
      repo_link: repoLink,
      install_dir: installDir,
      service_name: String(q("extra-repo-service-name")?.value || "").trim(),
      service_user: serviceUser,
      use_service: !!q("extra-repo-use-service")?.checked,
      autostart: !!q("extra-repo-autostart")?.checked,
    };
  }

  function deriveRepoNameFromLink(repoLink) {
    const raw = String(repoLink || "").trim().replace(/\/+$/, "");
    if (!raw) return "";
    const parts = raw.split("/");
    let tail = String(parts[parts.length - 1] || "").trim();
    if (tail.endsWith(".git")) tail = tail.slice(0, -4);
    return tail;
  }

  function deriveManagedRepoDefaultInstallDir(repoLink, serviceUser) {
    const link = String(repoLink || "").trim();
    if (!/^(https?:\/\/|git@|ssh:\/\/)/i.test(link)) return "";
    const repoName = deriveRepoNameFromLink(link);
    if (!repoName) return "";
    const user = String(serviceUser || "").trim() || String(q("stream-player-service-user")?.value || "").trim() || "djanebmb";
    return `/home/${user}/projects/${repoName}`;
  }

  function syncManagedRepoDerivedInstallDir() {
    const id = String(q("extra-repo-id")?.value || "").trim();
    if (id) return;
    const installEl = q("extra-repo-install-dir");
    if (!installEl) return;
    const current = String(installEl.value || "").trim();
    if (current) return;
    const repoLink = String(q("extra-repo-link")?.value || "").trim();
    const serviceUser = String(q("extra-repo-service-user")?.value || "").trim();
    const derived = deriveManagedRepoDefaultInstallDir(repoLink, serviceUser);
    if (derived) installEl.value = derived;
  }

  function setManagedRepoFormValues(item = {}) {
    const payload = item && typeof item === "object" ? item : {};
    const idEl = q("extra-repo-id");
    const nameEl = q("extra-repo-name");
    const linkEl = q("extra-repo-link");
    const installEl = q("extra-repo-install-dir");
    const serviceEl = q("extra-repo-service-name");
    const userEl = q("extra-repo-service-user");
    const useServiceEl = q("extra-repo-use-service");
    const autostartEl = q("extra-repo-autostart");
    if (idEl) idEl.value = String(payload.id || "");
    if (nameEl) nameEl.value = String(payload.name || "");
    if (linkEl) linkEl.value = String(payload.repo_link || payload.repo_dir || "");
    if (installEl) installEl.value = String(payload.install_dir || "");
    if (serviceEl) serviceEl.value = String(payload.service_name || "");
    if (userEl) userEl.value = String(payload.service_user || "");
    if (useServiceEl) useServiceEl.checked = payload.use_service !== false;
    if (autostartEl) autostartEl.checked = payload.autostart !== false;
  }

  function renderRepoInstallPathBrowser(data = {}) {
    repoInstallPathState.currentPath = String(data.current_path || "");
    repoInstallPathState.parentPath = String(data.parent_path || "");
    repoInstallPathState.rootPath = String(data.root_path || "");
    repoInstallPathState.roots = Array.isArray(data.roots) ? data.roots : [];

    const currentEl = q("repo-install-path-current");
    if (currentEl) currentEl.textContent = repoInstallPathState.currentPath || "-";
    const rootsHost = q("repo-install-path-roots");
    if (rootsHost) {
      const roots = Array.isArray(repoInstallPathState.roots) ? repoInstallPathState.roots : [];
      const btns = roots.map((rootPath) => {
        const rp = String(rootPath || "").trim();
        if (!rp) return "";
        const label = rp === repoInstallPathState.rootPath ? `${rp} (Root)` : rp;
        return `<button type="button" class="btn btn-outline-secondary btn-sm js-repo-path-root" data-path="${escapeHtml(rp)}">${escapeHtml(label)}</button>`;
      }).filter(Boolean);
      rootsHost.innerHTML = btns.length ? btns.join("") : "";
    }

    const host = q("repo-install-path-list");
    if (!host) return;
    const rows = [];
    if (repoInstallPathState.rootPath && repoInstallPathState.currentPath !== repoInstallPathState.rootPath) {
      rows.push(`<button type="button" class="list-group-item list-group-item-action js-repo-path-entry" data-path="${escapeHtml(repoInstallPathState.parentPath)}"><i class="bi bi-arrow-up-circle me-1"></i>..</button>`);
    }
    const dirs = Array.isArray(data.directories) ? data.directories : [];
    for (const dir of dirs) {
      const name = escapeHtml(String(dir.name || ""));
      const path = escapeHtml(String(dir.path || ""));
      rows.push(`<button type="button" class="list-group-item list-group-item-action js-repo-path-entry" data-path="${path}"><i class="bi bi-folder2 me-1"></i>${name}</button>`);
    }
    host.innerHTML = rows.length ? rows.join("") : '<div class="text-secondary p-2">Keine Unterordner.</div>';
  }

  async function refreshRepoInstallPathBrowser(path = "") {
    const query = new URLSearchParams();
    const targetPath = String(path || repoInstallPathState.currentPath || "").trim();
    if (targetPath) query.set("path", targetPath);
    let payload;
    try {
      payload = await fetchJson(`/api/stream/player/path-browser?${query.toString()}`, { timeoutMs: 10000 });
    } catch (_) {
      // Pfad existiert nicht (z.B. Laufwerk anders gemountet) — Root öffnen
      payload = await fetchJson(`/api/stream/player/path-browser`, { timeoutMs: 10000 });
    }
    renderRepoInstallPathBrowser((payload || {}).data || {});
  }

  async function openRepoInstallPathModal(targetInputId = "extra-repo-install-dir") {
    repoInstallPathState.targetInputId = targetInputId || "extra-repo-install-dir";
    const inputPath = String(q(repoInstallPathState.targetInputId)?.value || "").trim();
    await refreshRepoInstallPathBrowser(inputPath);
    const modalEl = q("repoInstallPathModal");
    if (!modalEl || !window.bootstrap || !window.bootstrap.Modal) return;
    repoInstallPathState.modal = window.bootstrap.Modal.getOrCreateInstance(modalEl);
    repoInstallPathState.modal.show();
  }

  function renderManagedRepos(items = []) {
    const host = q("extra-repos-list");
    if (!host) return;
    const list = (Array.isArray(items) ? items : []).slice().sort((a, b) => {
      const sa = (a && a.service_status && typeof a.service_status === "object") ? a.service_status : {};
      const sb = (b && b.service_status && typeof b.service_status === "object") ? b.service_status : {};
      const ia = sa.service_installed ? 1 : 0;
      const ib = sb.service_installed ? 1 : 0;
      if (ia !== ib) return ib - ia;
      return String((a && a.name) || "").localeCompare(String((b && b.name) || ""), "de");
    });
    managedInstallRepos = list;
    try {
      window.localStorage.setItem(MANAGED_REPOS_CACHE_KEY, JSON.stringify(list));
    } catch (_) {}
    if (!list.length) {
      host.innerHTML = '<div class="text-secondary">Keine zusätzlichen Repos hinterlegt.</div>';
      recomputeStreamFeatureState();
      applyStreamFeatureVisibility();
      return;
    }
    const cards = list.map((item, idx) => {
      const repoId = String(item.id || "");
      const name = escapeHtml(String(item.name || "-"));
      const useService = item.use_service !== false;
      const autostart = item.autostart !== false;
      const updateInfo = resolveRepoUpdateInfo(item);
      const hasRepoUpdate = !!(updateInfo && updateInfo.available);
      const status = (item.service_status && typeof item.service_status === "object") ? item.service_status : {};
      const isInstalled = !!(status.service_installed || status.service_running);
      const serviceInstalled = status.use_service === false ? "-" : (status.service_installed ? "ja" : "nein");
      const serviceRunning = status.use_service === false
        ? (status.runtime_reachable ? "ja" : "nein")
        : (status.service_running ? "ja" : "nein");
      const autostartInstalled = status.use_service === false ? "-" : (status.service_installed ? "ja" : "nein");
      const serviceAutostart = status.use_service === false
        ? "-"
        : (status.service_installed ? (status.service_enabled ? "ja" : "nein") : "-");
      const canControlService = useService;
      const isRunning = !!status.service_running;
      const controlLabel = isRunning ? "Stop" : "Start";
      const controlAction = isRunning ? "stop" : "start";
      const endpoints = (item.endpoints && typeof item.endpoints === "object") ? item.endpoints : {};
      const uiRaw = String(item.ui_url || item.web_url || endpoints.ui || "").trim();
      const apiBaseRaw = String(item.api_base_url || endpoints.api_base || "").trim();
      const healthRaw = String(item.health_url || endpoints.health || "").trim();
      let uiUrlRaw = "";
      if (uiRaw) {
        uiUrlRaw = uiRaw;
      } else if (apiBaseRaw) {
        uiUrlRaw = apiBaseRaw;
      } else if (healthRaw) {
        uiUrlRaw = healthRaw;
      }
      if (uiUrlRaw) {
        try {
          const parsed = new URL(uiUrlRaw, window.location.origin);
          if (parsed.pathname.endsWith("/health")) {
            parsed.pathname = parsed.pathname.slice(0, -"/health".length) || "/";
          }
          uiUrlRaw = parsed.toString();
        } catch (_) {
          if (uiUrlRaw.endsWith("/health")) {
            uiUrlRaw = uiUrlRaw.slice(0, -"/health".length) || "/";
          }
        }
      }
      const hasUiUrl = /^https?:\/\//i.test(uiUrlRaw);
      const uiUrl = escapeHtml(uiUrlRaw || "");
      const openButtonHtml = (isRunning && hasUiUrl)
        ? `<a class="btn btn-outline-info btn-sm" href="${uiUrl}" target="_blank" rel="noopener noreferrer">Öffnen</a>`
        : "";
      const installOrUninstallButtonHtml = isInstalled
        ? `<button class="btn btn-outline-danger btn-sm js-extra-repo-action" data-action="uninstall" data-id="${escapeHtml(repoId)}">Uninstall</button>`
        : `<button class="btn btn-outline-primary btn-sm js-extra-repo-action" data-action="install_update" data-id="${escapeHtml(repoId)}">Install</button>`;
      const reinstallButtonHtml = isInstalled
        ? `<button class="btn btn-outline-warning btn-sm js-extra-repo-action" data-action="reinstall" data-id="${escapeHtml(repoId)}" title="Service-Datei + git pull/clone neu durchführen (z.B. nach Laufwerk-Remount)"><i class="bi bi-arrow-repeat"></i> ReInstall</button>`
        : ``;
      const updateButtonHtml = (isInstalled && hasRepoUpdate)
        ? `<button class="btn btn-outline-primary btn-sm js-extra-repo-action" data-action="update" data-id="${escapeHtml(repoId)}">Update</button>`
        : "";
      const autostartButtonLabel = autostart ? "Autostart deaktivieren" : "Autostart aktivieren";
      const updateBadgeHtml = updateInfo
        ? (hasRepoUpdate
          ? '<span class="badge text-bg-warning border">Update: verfügbar</span>'
          : (String(updateInfo.error || "").trim()
            ? '<span class="badge text-bg-secondary border">Update: unbekannt</span>'
            : '<span class="badge text-bg-success border">Update: aktuell</span>'))
        : "";
      const checkboxHtml = hasRepoUpdate
        ? `<div class="form-check d-inline-flex align-items-center m-0">
             <input class="form-check-input js-repo-update-check me-1" type="checkbox"
               id="repo-upd-chk-${escapeHtml(repoId)}" data-id="${escapeHtml(repoId)}">
             <label class="form-check-label small text-warning fw-semibold" for="repo-upd-chk-${escapeHtml(repoId)}">
               auswählen
             </label>
           </div>`
        : "";
      return `
        <section class="mb-3">
          <div class="d-flex flex-wrap justify-content-between align-items-start gap-2 mb-2">
            <div class="d-flex align-items-center gap-2">
              ${checkboxHtml}
              <h3 class="h6 mb-0">${name}</h3>
            </div>
            <div class="d-flex flex-wrap gap-1">
              <span class="badge text-bg-light border">Service: ${useService ? "ja" : "nein"}</span>
              <span class="badge text-bg-light border">Installiert: ${serviceInstalled}</span>
              <span class="badge text-bg-light border">Läuft: ${serviceRunning}</span>
              <span class="badge text-bg-light border">Autostart installiert: ${autostartInstalled}</span>
              <span class="badge text-bg-light border">Autostart aktiv: ${serviceAutostart}</span>
              ${updateBadgeHtml}
            </div>
          </div>
          <div class="d-flex flex-wrap gap-2">
            ${openButtonHtml}
            <button class="btn btn-outline-dark btn-sm js-extra-repo-action" data-action="details" data-id="${escapeHtml(repoId)}">Details</button>
            <button class="btn btn-outline-secondary btn-sm js-extra-repo-action" data-action="edit" data-id="${escapeHtml(repoId)}"><i class="bi bi-pencil-square me-1"></i>Edit</button>
            <button class="btn btn-outline-success btn-sm js-extra-repo-action" data-action="service_action" data-service-action="${controlAction}" data-id="${escapeHtml(repoId)}" ${canControlService ? "" : "disabled"}>${controlLabel}</button>
            <button class="btn btn-outline-secondary btn-sm js-extra-repo-action" data-action="service_action" data-service-action="restart" data-id="${escapeHtml(repoId)}" ${canControlService ? "" : "disabled"}>Restart</button>
          </div>
          ${idx < list.length - 1 ? '<hr class="my-3">' : ''}
        </section>
      `;
    }).join("");
    host.innerHTML = cards;
    recomputeStreamFeatureState();
    applyStreamFeatureVisibility();
    renderHeroUpdateBadge((statusDashboardState.status || {}).app_update || {}, repoUpdatesState);
    updateBulkUpdateToolbar();
  }

  function updateBulkUpdateToolbar() {
    const toolbar = q("bulk-update-toolbar");
    const countEl = q("bulk-update-selection-count");
    const countBtnEl = q("bulk-update-repo-count");
    const btn = q("btn-bulk-update-repos");
    const selectAll = q("bulk-select-all-updates");
    const allChecks = document.querySelectorAll(".js-repo-update-check:not(:disabled)");
    const checkedChecks = document.querySelectorAll(".js-repo-update-check:checked");
    const selectedCount = checkedChecks.length;
    if (countEl) countEl.textContent = `${selectedCount} ausgewählt`;
    if (countBtnEl) countBtnEl.textContent = String(selectedCount);
    if (btn) btn.disabled = selectedCount === 0;
    if (selectAll && allChecks.length > 0) {
      selectAll.indeterminate = selectedCount > 0 && selectedCount < allChecks.length;
      selectAll.checked = selectedCount === allChecks.length;
    }
  }

  async function bulkUpdateSelectedRepos() {
    const checks = document.querySelectorAll(".js-repo-update-check:checked");
    const ids = Array.from(checks).map((c) => String(c.dataset.id || "").trim()).filter(Boolean);
    if (!ids.length) return;
    let failed = 0;
    for (const id of ids) {
      try {
        if (id === "__portal__") {
          await updatePortal();
        } else if (id === "__display_player__") {
          await startStreamPlayerInstallUpdate();
        } else {
          await startManagedRepoInstallUpdate(id);
        }
      } catch (e) {
        failed += 1;
        let name = id;
        if (id === "__portal__") name = "Device-Portal";
        else if (id === "__display_player__") name = "DisplayPlayer";
        else { const target = getManagedRepoById(id); name = String((target && target.name) || id); }
        toast(`Update fehlgeschlagen: ${name}`, "danger");
      }
    }
    await refreshStatus();
    if (failed === 0) {
      toast(`${ids.length} Repo(s) erfolgreich aktualisiert`, "success");
    }
  }

  function renderManagedReposFromCache() {
    try {
      const raw = window.localStorage.getItem(MANAGED_REPOS_CACHE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return;
      renderManagedRepos(parsed);
    } catch (_) {}
  }

  function renderAutodiscoverServices(items = []) {
    const host = q("autodiscover-services-list");
    if (!host) return;
    const list = Array.isArray(items) ? items : [];
    autodiscoverServices = list;
    if (!list.length) {
      host.innerHTML = '<div class="text-secondary">Keine Autodiscover-Einträge.</div>';
      recomputeStreamFeatureState();
      applyStreamFeatureVisibility();
      return;
    }
    const rows = list.map((item) => {
      const id = escapeHtml(String(item.id || ""));
      const name = escapeHtml(String(item.repo_name || "-"));
      const link = escapeHtml(String(item.repo_link || "-"));
      const hostName = escapeHtml(String(item.hostname || item.remote_addr || "-"));
      const seen = escapeHtml(String(item.last_seen_at || item.updated_at || "-"));
      return `<tr>
        <td class="fw-semibold">${name}</td>
        <td class="text-break">${link}</td>
        <td>${hostName}</td>
        <td>${seen}</td>
        <td><button class="btn btn-outline-primary btn-sm js-autodiscover-action" data-action="promote" data-id="${id}">Übernehmen</button></td>
      </tr>`;
    }).join("");
    host.innerHTML = `<div class="table-responsive"><table class="table table-sm align-middle mb-0"><thead><tr><th>Name</th><th>Repo</th><th>Host</th><th>Last Seen</th><th>Aktion</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    recomputeStreamFeatureState();
    applyStreamFeatureVisibility();
  }

  async function refreshAutodiscoverServices() {
    const payload = await fetchJson("/api/autodiscover/services", { timeoutMs: 10000, cache: "no-store" });
    const services = (((payload || {}).data || {}).services) || [];
    renderAutodiscoverServices(services);
  }

  async function promoteAutodiscoveredService(serviceId) {
    const payload = await fetchJson(`/api/autodiscover/services/${encodeURIComponent(String(serviceId || "").trim())}/promote`, {
      method: "POST",
      timeoutMs: 12000,
    });
    const repos = ((((payload || {}).data || {}).repos) || []);
    renderManagedRepos(repos);
    await refreshManagedRepos();
    toast("Autodiscover-Service übernommen", "success");
  }

  async function refreshManagedRepos(live = true) {
    const suffix = live ? "?live=1" : "";
    const payload = await fetchJson(`/api/stream/player/repos${suffix}`, { timeoutMs: 10000 });
    const repos = (((payload || {}).data || {}).repos) || [];
    renderManagedRepos(repos);
    if (statusDashboardState.status) {
      renderLlmManagerCard(statusDashboardState.status, llmManagerState.info || {});
    }
  }

  async function saveManagedRepo() {
    const form = getManagedRepoFormValues();
    if (!form.repo_link) {
      throw new Error("Bitte Repo Link/Pfad angeben.");
    }
    const payload = await fetchJson("/api/stream/player/repos", {
      method: "POST",
      body: form,
      timeoutMs: 12000,
    });
    const repos = ((((payload || {}).data || {}).repos) || []);
    renderManagedRepos(repos);
    await refreshManagedRepos();
    setManagedRepoFormValues({});
    toast("Zusätzliches Repo gespeichert", "success");
  }

  function getManagedRepoById(repoId) {
    const target = String(repoId || "").trim();
    if (!target) return null;
    return managedInstallRepos.find((item) => String(item.id || "") === target) || null;
  }

  async function deleteManagedRepo(repoId) {
    const target = getManagedRepoById(repoId);
    const label = target ? String(target.name || target.repo_link || "Repo") : "Repo";
    if (!window.confirm(`${label} wirklich löschen?`)) return;
    const payload = await fetchJson(`/api/stream/player/repos/${encodeURIComponent(String(repoId || "").trim())}/delete`, {
      method: "POST",
      timeoutMs: 12000,
    });
    const repos = ((((payload || {}).data || {}).repos) || []);
    renderManagedRepos(repos);
    await refreshManagedRepos();
    const form = getManagedRepoFormValues();
    if (form.id && form.id === String(repoId || "").trim()) {
      setManagedRepoFormValues({});
    }
    toast("Repo entfernt", "success");
  }

  async function startManagedRepoInstallUpdate(repoId) {
    const target = getManagedRepoById(repoId);
    if (!target) {
      throw new Error("Repo nicht gefunden.");
    }
    const repoName = String(target.name || target.repo_link || "Repo");
    openUpdateModal(repoName);
    _pendingHistoryMeta = {
      kind: "managed_repo",
      name: repoName,
      service_name: String(target.service_name || ""),
    };
    const payload = await fetchJson(`/api/stream/player/repos/${encodeURIComponent(String(repoId || "").trim())}/install-update`, {
      method: "POST",
      timeoutMs: 20000,
    });
    const data = payload.data || {};
    streamPlayerUpdateJobId = String(data.job_id || "").trim();
    await pollStreamPlayerUpdateStatus(streamPlayerUpdateJobId);
    await refreshManagedRepos(true);
    toast(`${repoName} Install/Update gestartet`, "success");
  }

  function openRepoChangePathModal(repoId) {
    const target = getManagedRepoById(repoId);
    if (!target) throw new Error("Repo nicht gefunden.");
    _repoChangePathRepoId = repoId;
    const idInput = q("change-path-repo-id");
    if (idInput) idInput.value = repoId;
    const nameEl = q("change-path-repo-name");
    if (nameEl) nameEl.textContent = String(target.name || target.repo_link || "-");
    const currentEl = q("change-path-current-dir");
    if (currentEl) currentEl.textContent = String(target.install_dir || "-");
    const newInput = q("change-path-install-dir");
    if (newInput) newInput.value = String(target.install_dir || "");
    const modalEl = q("repoChangePathModal");
    if (!modalEl || !window.bootstrap || !window.bootstrap.Modal) return;
    window.bootstrap.Modal.getOrCreateInstance(modalEl).show();
  }

  function openRepoEditModal(repoId) {
    const target = getManagedRepoById(repoId);
    if (!target) throw new Error("Repo nicht gefunden.");

    const idInput = q("repo-edit-modal-repo-id");
    if (idInput) idInput.value = repoId;
    const titleEl = q("repo-edit-modal-title");
    if (titleEl) titleEl.textContent = String(target.name || "Repo bearbeiten");
    const linkEl = q("repo-edit-modal-repo-link");
    if (linkEl) linkEl.textContent = String(target.repo_link || "-");

    // Autostart-Button Label
    const autostart = target.autostart !== false;
    const autostartLabelEl = q("repo-edit-autostart-label");
    if (autostartLabelEl) autostartLabelEl.textContent = autostart ? "Autostart deaktivieren" : "Autostart aktivieren";
    const autostartBtn = q("btn-repo-edit-autostart");
    if (autostartBtn) {
      autostartBtn.dataset.enabled = autostart ? "0" : "1";
      autostartBtn.dataset.id = repoId;
    }

    // Update-Button: nur aktiv wenn Update verfügbar
    const updateInfo = resolveRepoUpdateInfo(target);
    const updateBtn = q("btn-repo-edit-update");
    if (updateBtn) {
      updateBtn.disabled = !(updateInfo && updateInfo.available);
      updateBtn.dataset.id = repoId;
    }

    // Uninstall/Install-Button
    const status = (target.service_status && typeof target.service_status === "object") ? target.service_status : {};
    const isInstalled = !!(status.service_installed || status.service_running);
    const uninstallBtn = q("btn-repo-edit-uninstall");
    if (uninstallBtn) {
      uninstallBtn.textContent = "";
      const icon = document.createElement("i");
      icon.className = isInstalled ? "bi bi-box-arrow-down me-2" : "bi bi-box-arrow-in-down me-2";
      uninstallBtn.appendChild(icon);
      uninstallBtn.appendChild(document.createTextNode(isInstalled ? "Uninstall" : "Install"));
      uninstallBtn.dataset.action = isInstalled ? "uninstall" : "install";
      uninstallBtn.dataset.id = repoId;
    }

    // Pfad + ReInstall + Delete Buttons dataset
    for (const btnId of ["btn-repo-edit-change-path", "btn-repo-edit-reinstall", "btn-repo-edit-delete"]) {
      const btn = q(btnId);
      if (btn) btn.dataset.id = repoId;
    }

    const modalEl = q("repoEditModal");
    if (!modalEl || !window.bootstrap || !window.bootstrap.Modal) return;
    window.bootstrap.Modal.getOrCreateInstance(modalEl).show();
  }

  function _closeEditModal() {
    const modalEl = q("repoEditModal");
    if (modalEl && window.bootstrap && window.bootstrap.Modal) {
      window.bootstrap.Modal.getOrCreateInstance(modalEl).hide();
    }
  }

  function openRepoReinstallModal(repoId) {
    const target = getManagedRepoById(repoId);
    if (!target) throw new Error("Repo nicht gefunden.");
    const idInput = q("reinstall-repo-id");
    if (idInput) idInput.value = repoId;
    const nameEl = q("reinstall-repo-name");
    if (nameEl) nameEl.textContent = String(target.name || "-");
    const linkEl = q("reinstall-repo-link");
    if (linkEl) linkEl.textContent = String(target.repo_link || "-");
    const dirInput = q("reinstall-install-dir");
    if (dirInput) {
      dirInput.value = String(target.install_dir || "");
      dirInput._originalValue = String(target.install_dir || "");
    }
    const hint = q("reinstall-path-changed-hint");
    if (hint) hint.classList.add("d-none");
    const modalEl = q("repoReinstallModal");
    if (!modalEl || !window.bootstrap || !window.bootstrap.Modal) return;
    window.bootstrap.Modal.getOrCreateInstance(modalEl).show();
  }

  async function startRepoReinstall(repoId) {
    const newPath = String(q("reinstall-install-dir")?.value || "").trim();
    if (!repoId) throw new Error("Repo-ID fehlt.");
    if (!newPath) throw new Error("Bitte einen Installationspfad eingeben.");
    const target = getManagedRepoById(repoId);
    const currentPath = String((target || {}).install_dir || "").trim();
    const modalEl = q("repoReinstallModal");
    if (modalEl && window.bootstrap && window.bootstrap.Modal) {
      window.bootstrap.Modal.getOrCreateInstance(modalEl).hide();
    }
    if (newPath !== currentPath) {
      await fetchJson(
        `/api/stream/player/repos/${encodeURIComponent(repoId)}/set-path`,
        { method: "POST", body: { install_dir: newPath }, timeoutMs: 8000 }
      );
      toast(`Pfad gespeichert: ${newPath}`, "success");
    }
    await startManagedRepoInstallUpdate(repoId);
    await refreshManagedRepos(true);
  }

  let _basePathPreviewData = null;

  async function openRepoBasePathModal() {
    _basePathPreviewData = null;
    const previewWrap = q("base-path-preview-wrap");
    if (previewWrap) previewWrap.classList.add("d-none");
    const saveBtn = q("btn-base-path-save");
    const saveReinstallBtn = q("btn-base-path-save-reinstall");
    if (saveBtn) saveBtn.disabled = true;
    if (saveReinstallBtn) saveReinstallBtn.disabled = true;

    // Aktuellen Basis-Pfad laden
    try {
      const payload = await fetchJson("/api/stream/player/repos/base-path", { timeoutMs: 5000 });
      const current = String(((payload || {}).data || {}).managed_install_base || "").trim();
      const currentEl = q("base-path-current");
      if (currentEl) currentEl.textContent = current || "(nicht gesetzt)";
      const input = q("base-path-input");
      if (input && !input.value) input.value = current;
    } catch (_) {}

    const modalEl = q("repoBasePathModal");
    if (!modalEl || !window.bootstrap || !window.bootstrap.Modal) return;
    window.bootstrap.Modal.getOrCreateInstance(modalEl).show();
  }

  async function loadBasePathPreview() {
    const newBase = String(q("base-path-input")?.value || "").trim();
    if (!newBase) { toast("Bitte einen Basis-Pfad eingeben.", "warning"); return; }
    const payload = await fetchJson("/api/stream/player/repos/preview-base-path", {
      method: "POST", body: { managed_install_base: newBase }, timeoutMs: 8000,
    });
    const data = (payload || {}).data || {};
    _basePathPreviewData = data;
    const preview = Array.isArray(data.preview) ? data.preview : [];
    const tbody = q("base-path-preview-body");
    const noChanges = q("base-path-no-changes");
    const previewWrap = q("base-path-preview-wrap");
    if (previewWrap) previewWrap.classList.remove("d-none");

    const changed = preview.filter((r) => r.changed);
    if (noChanges) noChanges.classList.toggle("d-none", changed.length > 0);
    if (tbody) {
      tbody.innerHTML = preview.map((r) => {
        const rowClass = r.changed ? "" : "text-secondary";
        const arrow = r.changed
          ? `<i class="bi bi-arrow-right text-warning"></i>`
          : `<i class="bi bi-dash text-secondary"></i>`;
        return `<tr class="${rowClass}">
          <td class="fw-semibold">${escapeHtml(String(r.name || "-"))}</td>
          <td class="font-monospace">${escapeHtml(String(r.old_dir || "-"))}</td>
          <td class="text-center">${arrow}</td>
          <td class="font-monospace ${r.changed ? "text-success" : ""}">${escapeHtml(String(r.new_dir || "-"))}</td>
        </tr>`;
      }).join("") || `<tr><td colspan="4" class="text-secondary">Keine Module gefunden.</td></tr>`;
    }
    const hasChanges = changed.length > 0;
    const saveBtn = q("btn-base-path-save");
    const saveReinstallBtn = q("btn-base-path-save-reinstall");
    if (saveBtn) saveBtn.disabled = !hasChanges;
    if (saveReinstallBtn) saveReinstallBtn.disabled = !hasChanges;
  }

  async function saveBasePath(andReinstall = false) {
    const newBase = String(q("base-path-input")?.value || "").trim();
    if (!newBase) throw new Error("Basis-Pfad fehlt.");
    const payload = await fetchJson("/api/stream/player/repos/base-path", {
      method: "POST", body: { managed_install_base: newBase }, timeoutMs: 10000,
    });
    const data = (payload || {}).data || {};
    const changes = Array.isArray(data.changes) ? data.changes : [];
    if (Array.isArray(data.repos)) renderManagedRepos(data.repos);

    const modalEl = q("repoBasePathModal");
    if (modalEl && window.bootstrap && window.bootstrap.Modal) {
      window.bootstrap.Modal.getOrCreateInstance(modalEl).hide();
    }
    toast(`Standard-Pfad gespeichert. ${changes.length} Modul-Pfad(e) aktualisiert.`, "success");

    if (andReinstall && changes.length > 0) {
      for (const change of changes) {
        if (!change.id) continue;
        try {
          await startManagedRepoInstallUpdate(change.id);
        } catch (err) {
          toast(`ReInstall fehlgeschlagen: ${escapeHtml(String(change.name || change.id))} — ${escapeHtml(String(err?.message || err))}`, "danger");
        }
      }
      await refreshManagedRepos(true);
    }
  }

  async function saveRepoPath(repoId, newPath, andReinstall = false) {
    if (!repoId || !newPath) throw new Error("Repo-ID oder Pfad fehlt.");
    const payload = await fetchJson(
      `/api/stream/player/repos/${encodeURIComponent(repoId)}/set-path`,
      { method: "POST", body: { install_dir: newPath }, timeoutMs: 8000 }
    );
    const updated = ((payload || {}).data || {}).item || {};
    const list = ((payload || {}).data || {}).repos;
    if (Array.isArray(list)) renderManagedRepos(list);
    const modalEl = q("repoChangePathModal");
    if (modalEl && window.bootstrap && window.bootstrap.Modal) {
      window.bootstrap.Modal.getOrCreateInstance(modalEl).hide();
    }
    toast(`Pfad gespeichert: ${newPath}`, "success");
    if (andReinstall) {
      await startManagedRepoInstallUpdate(repoId);
    }
    await refreshManagedRepos(true);
    return updated;
  }

  async function setManagedRepoAsPlayer(repoId) {
    const target = getManagedRepoById(repoId);
    if (!target) {
      throw new Error("Repo nicht gefunden.");
    }
    const repoLink = String(target.repo_link || "").trim();
    if (!repoLink) {
      throw new Error("Repo-Link fehlt.");
    }
    const serviceName = String(target.service_name || "").trim() || DEFAULT_PLAYER_SERVICE_NAME;
    const serviceUser = String(target.service_user || "").trim();
    const installDir = String(target.install_dir || "").trim();
    await fetchJson("/api/stream/player/repo", {
      method: "POST",
      body: {
        player_repo_link: repoLink,
        player_service_name: serviceName,
        player_service_user: serviceUser,
        player_install_dir: installDir,
      },
      timeoutMs: 12000,
    });
    await loadPlayerRepoConfig();
    toast(`Als Stream-Player gesetzt: ${String(target.name || repoLink)}`, "success");
  }

  function showManagedRepoDetails(repoId) {
    const target = getManagedRepoById(repoId);
    if (!target) {
      throw new Error("Repo nicht gefunden.");
    }
    const put = (id, value) => {
      const el = q(id);
      if (el) el.textContent = String(value || "-");
    };
    put("managed-repo-details-name", target.name || "-");
    put("managed-repo-details-repo", target.repo_link || "-");
    put("managed-repo-details-install-dir", target.install_dir || "-");
    put("managed-repo-details-service", target.service_name || "-");
    put("managed-repo-details-user", target.service_user || "-");
    const svc = (target.service_status && typeof target.service_status === "object") ? target.service_status : {};
    put("managed-repo-details-service-installed", target.use_service === false ? "-" : (svc.service_installed ? "ja" : "nein"));
    put("managed-repo-details-service-running", target.use_service === false
      ? (svc.runtime_reachable ? "ja (Health)" : "nein")
      : (svc.service_running ? "ja" : "nein"));
    put("managed-repo-details-service-autostart", target.use_service === false ? "-" : (svc.service_enabled ? "ja" : "nein"));
    const endpoints = (target.endpoints && typeof target.endpoints === "object") ? target.endpoints : {};
    put("managed-repo-details-api-base", target.api_base_url || endpoints.api_base || "-");
    put("managed-repo-details-health-url", target.health_url || endpoints.health || "-");
    put("managed-repo-details-ui-url", target.ui_url || target.web_url || endpoints.ui || "-");
    put("managed-repo-details-port", target.service_port ?? "-");
    put("managed-repo-details-hostname", target.hostname || "-");
    put("managed-repo-details-node-name", target.node_name || "-");
    put("managed-repo-details-source", target.source || "-");
    put("managed-repo-details-first-seen", target.first_seen_at || "-");
    put("managed-repo-details-last-seen", target.last_seen_at || "-");
    const raw = q("managed-repo-details-json");
    if (raw) {
      raw.textContent = JSON.stringify(target, null, 2);
    }
    const modalEl = q("managedRepoDetailsModal");
    if (modalEl && window.bootstrap && window.bootstrap.Modal) {
      window.bootstrap.Modal.getOrCreateInstance(modalEl).show();
    }
  }

  async function toggleManagedRepoAutostart(repoId, enabled) {
    const payload = await fetchJson(`/api/stream/player/repos/${encodeURIComponent(String(repoId || "").trim())}/service-autostart`, {
      method: "POST",
      body: { enabled: !!enabled },
      timeoutMs: 20000,
    });
    const repo = payload?.data?.repo || null;
    if (repo) {
      const idx = managedInstallRepos.findIndex((item) => String(item.id || "") === String(repo.id || ""));
      if (idx >= 0) {
        managedInstallRepos[idx] = repo;
      }
      renderManagedRepos(managedInstallRepos);
    } else {
      await refreshManagedRepos();
    }
    toast(`Autostart ${enabled ? "aktiviert" : "deaktiviert"}`, "success");
  }

  async function uninstallManagedRepo(repoId) {
    const target = getManagedRepoById(repoId);
    const label = target ? String(target.name || target.repo_link || "Repo") : "Repo";
    const removeRepo = !!window.confirm(`${label}: Repo-Verzeichnis auch löschen?\nOK = mit Repo-Ordner löschen\nAbbrechen = nur Service entfernen`);
    const payload = await fetchJson(`/api/stream/player/repos/${encodeURIComponent(String(repoId || "").trim())}/uninstall`, {
      method: "POST",
      body: { remove_repo: removeRepo },
      timeoutMs: 30000,
    });
    const action = payload?.data?.action || {};
    toast(`Uninstall abgeschlossen${action.removed_repo ? " (Repo entfernt)" : ""}`, "success");
  }

  async function controlManagedRepoService(repoId, action) {
    const serviceAction = String(action || "").trim().toLowerCase();
    if (!["start", "stop", "restart", "status"].includes(serviceAction)) {
      throw new Error("Ungültige Service-Aktion.");
    }
    try {
      const payload = await fetchJson(`/api/stream/player/repos/${encodeURIComponent(String(repoId || "").trim())}/service-action`, {
        method: "POST",
        body: { action: serviceAction },
        timeoutMs: 20000,
      });
      const repo = payload?.data?.repo || null;
      if (repo) {
        const idx = managedInstallRepos.findIndex((item) => String(item.id || "") === String(repo.id || ""));
        if (idx >= 0) {
          managedInstallRepos[idx] = repo;
        }
        renderManagedRepos(managedInstallRepos);
      } else {
        await refreshManagedRepos(true);
      }
      toast(`Service: ${serviceAction}`, "success");
    } catch (err) {
      await refreshManagedRepos(true);
      throw err;
    }
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

  function getUpdateLogEl() {
    return q("system-update-log") || q("stream-player-update-log");
  }

  // ---------------------------------------------------------------------------
  // Update log modal helpers
  // ---------------------------------------------------------------------------

  function _getUpdateModal() {
    const el = document.getElementById("updateLogModal");
    if (!el) return null;
    if (!_updateModalInstance) {
      _updateModalInstance = bootstrap.Modal.getOrCreateInstance(el);
    }
    return _updateModalInstance;
  }

  function openUpdateModal(title = "Update") {
    const modal = _getUpdateModal();
    if (!modal) return;
    const titleEl = document.getElementById("updateLogModalTitle");
    const logEl = document.getElementById("update-modal-log");
    const badge = document.getElementById("update-modal-status-badge");
    const footerInfo = document.getElementById("update-modal-footer-info");
    const closeBtn = document.getElementById("update-modal-close-btn");
    const closeX = document.getElementById("update-modal-close-x");
    if (titleEl) titleEl.textContent = title;
    if (logEl) logEl.textContent = "Starte…";
    if (badge) { badge.textContent = "läuft"; badge.className = "badge text-bg-warning"; }
    if (footerInfo) footerInfo.textContent = "";
    if (closeBtn) closeBtn.disabled = true;
    if (closeX) closeX.disabled = true;
    _updateModalActive = true;
    _updateModalStartTs = Date.now();
    modal.show();
  }

  function _updateModalLog(text) {
    const logEl = document.getElementById("update-modal-log");
    if (!logEl) return;
    const atBottom = isNearBottom(logEl);
    logEl.textContent = text;
    if (atBottom) logEl.scrollTop = logEl.scrollHeight;
  }

  function _setUpdateModalStatus(status, success) {
    const badge = document.getElementById("update-modal-status-badge");
    const closeBtn = document.getElementById("update-modal-close-btn");
    const closeX = document.getElementById("update-modal-close-x");
    const footerInfo = document.getElementById("update-modal-footer-info");
    const done = status === "done" || status === "failed";
    if (badge) {
      if (status === "done" && success) { badge.textContent = "Erfolg"; badge.className = "badge text-bg-success"; }
      else if (status === "failed" || (status === "done" && !success)) { badge.textContent = "Fehler"; badge.className = "badge text-bg-danger"; }
      else if (status === "restarting") { badge.textContent = "Neustart"; badge.className = "badge text-bg-info"; }
      else { badge.textContent = "läuft"; badge.className = "badge text-bg-warning"; }
    }
    if (done) {
      _updateModalActive = false;
      if (closeBtn) closeBtn.disabled = false;
      if (closeX) closeX.disabled = false;
      if (footerInfo && _updateModalStartTs) {
        const secs = Math.round((Date.now() - _updateModalStartTs) / 1000);
        footerInfo.textContent = `Dauer: ${secs}s`;
      }
      const modal = _getUpdateModal();
      if (modal) setTimeout(() => modal.hide(), 3000);
    }
  }

  // ---------------------------------------------------------------------------
  // Update history
  // ---------------------------------------------------------------------------

  async function recordUpdateHistory(entry) {
    try {
      await fetchJson("/api/update-history/record", { method: "POST", body: entry, timeoutMs: 5000 });
    } catch (_) { /* non-critical */ }
    runQuiet(fetchAndRenderUpdateHistory);
  }

  async function fetchAndRenderUpdateHistory() {
    try {
      const payload = await fetchJson("/api/update-history", { timeoutMs: 6000, cache: "no-store" });
      renderUpdateHistory(Array.isArray(payload.items) ? payload.items : []);
    } catch (_) { /* silent */ }
  }

  function renderUpdateHistory(items) {
    const host = document.getElementById("update-history-list");
    const clearBtn = document.getElementById("btn-clear-update-history");
    if (!host) return;
    if (!items || !items.length) {
      host.innerHTML = '<div class="text-secondary">Noch keine Updates durchgeführt.</div>';
      if (clearBtn) clearBtn.classList.add("d-none");
      return;
    }
    if (clearBtn) clearBtn.classList.remove("d-none");
    const rows = items.map((item) => {
      const isOk = item.success || item.status === "done";
      const badgeClass = item.status === "done" && item.success ? "text-bg-success"
        : item.status === "failed" ? "text-bg-danger"
        : "text-bg-secondary";
      const label = item.status === "done" && item.success ? "OK"
        : item.status === "failed" ? "Fehler"
        : item.status || "?";
      const ts = item.ts ? new Date(item.ts).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" }) : "—";
      const commits = (item.before_commit || item.after_commit)
        ? `<span class="text-secondary">${item.before_commit || "?"} → ${item.after_commit || "?"}</span>`
        : "";
      const kindIcon = item.kind === "portal" ? "🔧" : item.kind === "player" ? "▶️" : "📦";
      const svcStr = item.service_name ? `<span class="text-secondary">${item.service_name}</span>` : "";
      const durationStr = item.duration ? `<span class="text-secondary ms-2">${item.duration}</span>` : "";
      return `<tr>
        <td class="text-nowrap pe-2 text-secondary" style="width:1%;white-space:nowrap">${ts}</td>
        <td class="pe-2">${kindIcon} <span class="fw-semibold">${item.name || "—"}</span>${svcStr ? " " + svcStr : ""}</td>
        <td class="pe-2"><span class="badge ${badgeClass}">${label}</span>${durationStr}</td>
        <td class="text-nowrap text-secondary">${commits}</td>
      </tr>`;
    }).join("");
    host.innerHTML = `<table class="table table-sm table-borderless mb-0"><tbody>${rows}</tbody></table>`;
  }

  function renderStreamPlayerUpdateStatus(data) {
    const logEl = getUpdateLogEl();
    if (!logEl) return;
    const shouldStickBottom = isNearBottom(logEl);
    const status = String((data || {}).status || "unknown");
    const lines = [
      `status: ${status}`,
      `success: ${String(!!(data || {}).success)}`,
      `message: ${(data || {}).message || "-"}`,
      `job_id: ${(data || {}).job_id || "-"}`,
      `repo: ${(data || {}).repo_dir || "-"}`,
      `repo_link: ${(data || {}).repo_link || "-"}`,
      `install_dir: ${(data || {}).install_dir || "-"}`,
      `user: ${(data || {}).service_user || "-"}`,
      `service: ${(data || {}).service_name || "-"}`,
      `use_service: ${String((data || {}).use_service ?? true)}`,
      `autostart: ${String((data || {}).autostart ?? true)}`,
      `git_status: ${(data || {}).git_status || "-"}`,
      `commit: ${((data || {}).before_commit || "-")} -> ${((data || {}).after_commit || "-")}`,
      `started_at: ${(data || {}).started_at || "-"}`,
      `finished_at: ${(data || {}).finished_at || "-"}`,
      "",
      "log:",
      (data || {}).log || "-",
    ];
    logEl.textContent = lines.join("\n");
    if (shouldStickBottom) {
      logEl.scrollTop = logEl.scrollHeight;
    }
    if (_updateModalActive || status === "done" || status === "failed") {
      _updateModalLog((data || {}).log || lines.join("\n"));
      _setUpdateModalStatus(status, !!(data || {}).success);
    }
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
            const meta = _pendingHistoryMeta;
            _pendingHistoryMeta = null;
            if (meta) {
              recordUpdateHistory({
                kind: meta.kind || "managed_repo",
                name: meta.name || jobId || "Repo",
                status: nextStatus,
                success: !!data.success,
                before_commit: data.before_commit || "",
                after_commit: data.after_commit || "",
                service_name: meta.service_name || data.service_name || "",
                ts: new Date().toISOString(),
              });
            }
          }
        } catch (_) {
          if (streamPlayerUpdatePollHandle) {
            window.clearInterval(streamPlayerUpdatePollHandle);
            streamPlayerUpdatePollHandle = null;
          }
        }
      }, 3500);
    } else if (status === "done" || status === "failed") {
      const meta = _pendingHistoryMeta;
      _pendingHistoryMeta = null;
      if (meta) {
        recordUpdateHistory({
          kind: meta.kind || "managed_repo",
          name: meta.name || jobId || "Repo",
          status,
          success: !!first.success,
          before_commit: first.before_commit || "",
          after_commit: first.after_commit || "",
          service_name: meta.service_name || first.service_name || "",
          ts: new Date().toISOString(),
        });
      }
    }
  }

  async function startStreamPlayerInstallUpdate() {
    const { player_repo_link, player_service_name, player_service_user } = getPlayerFormValues();
    if (!player_repo_link) {
      throw new Error("Bitte zuerst Player-Repo Link/Pfad setzen.");
    }
    const playerName = player_repo_link.split("/").pop() || "Player";
    openUpdateModal(`Player: ${playerName}`);
    _pendingHistoryMeta = {
      kind: "player",
      name: playerName,
      service_name: String(player_service_name || ""),
    };
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
    await refreshSystemUpdateSummaries();
    toast(`Player ${action}`, "success");
  }

  async function portalServiceAction(action) {
    const safeAction = String(action || "").trim().toLowerCase();
    if (!["start", "stop"].includes(safeAction)) {
      throw new Error("Ungültige Portal-Service-Aktion.");
    }
    await fetchJson("/api/system/service/action", {
      method: "POST",
      body: { service_name: "device-portal.service", action: safeAction },
      timeoutMs: 12000,
    });
    await refreshSystemUpdateSummaries();
    toast(`Portal ${safeAction}`, "success");
  }

  async function installPlayerService() {
    const { player_repo_link, player_service_name, player_service_user } = getPlayerFormValues();
    if (!player_repo_link) {
      throw new Error("Bitte zuerst Player-Repo Link/Pfad setzen.");
    }
    if (!player_service_user) {
      throw new Error("Bitte Service-User setzen.");
    }
    const btn = q("btn-player-service-install");
    const original = btn ? btn.innerHTML : "";
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Installiere...';
    }
    try {
      await fetchJson("/api/stream/player/service/install", {
        method: "POST",
        body: { player_repo_link, player_service_name, player_service_user },
        timeoutMs: 20000,
      });
      await refreshPlayerStatus();
      toast("Player Service installiert", "success");
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = original;
      }
    }
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

    const actionsHost = q("bt-pairing-actions");
    if (actionsHost) {
      clearNode(actionsHost);
      const mac = deviceMac || "";
      if (mac) {
        const pairBtn = document.createElement("button");
        pairBtn.className = "btn btn-outline-success btn-sm";
        pairBtn.textContent = "Koppeln & Verbinden";
        pairBtn.addEventListener("click", () => run(async () => {
          await bluetoothDeviceAction("pair", mac);
          await setAudioOutput(`bluetooth:${mac}`);
          const modalEl = q("btPairingModal");
          if (modalEl) {
            bootstrap.Modal.getOrCreateInstance(modalEl).hide();
          }
        }));
        actionsHost.append(pairBtn);
      }
    }
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

  function btStatusBadge(text, type) {
    const cls = type === "ok" ? "text-bg-success" : (type === "warn" ? "text-bg-warning" : "text-bg-secondary");
    return `<span class="badge ${cls} me-1">${escapeHtml(text)}</span>`;
  }

  function renderBluetoothDeviceRows(hostId, devices, source) {
    const host = q(hostId);
    if (!host) return;
    clearNode(host);
    if (!Array.isArray(devices) || devices.length === 0) {
      const empty = document.createElement("div");
      empty.className = "text-secondary";
      empty.textContent = source === "scan" ? "Keine Scan-Ergebnisse." : "Keine Bluetooth-Geräte gefunden.";
      host.append(empty);
      return;
    }
    for (const item of devices) {
      const id = String(item.id || "").toUpperCase();
      const name = String(item.name || id || "Bluetooth Device");
      const paired = !!item.paired;
      const connected = !!item.connected;
      const trusted = !!item.trusted;
      const audioCapable = !!item.audio_capable;
      const isCurrentOutput = !!item.is_current_output;

      const row = document.createElement("div");
      row.className = "list-group-item px-0";

      const top = document.createElement("div");
      top.className = "d-flex flex-wrap justify-content-between align-items-center gap-2";

      const left = document.createElement("div");
      left.innerHTML = `<strong>${escapeHtml(name)}</strong><div class="small text-secondary">${escapeHtml(id)}</div>`;

      const right = document.createElement("div");
      right.innerHTML = `
        ${btStatusBadge(paired ? "paired" : "unpaired", paired ? "ok" : "warn")}
        ${btStatusBadge(trusted ? "trusted" : "untrusted", trusted ? "ok" : "secondary")}
        ${btStatusBadge(connected ? "connected" : "disconnected", connected ? "ok" : "secondary")}
        ${btStatusBadge(audioCapable ? "audio" : "no-audio", audioCapable ? "ok" : "warn")}
        ${isCurrentOutput ? btStatusBadge("output", "ok") : ""}
      `;
      top.append(left, right);

      const actions = document.createElement("div");
      actions.className = "d-flex flex-wrap gap-2 mt-2";

      if (!paired || source === "scan") {
        const pairBtn = document.createElement("button");
        pairBtn.className = "btn btn-outline-primary btn-sm";
        pairBtn.textContent = "Pair";
        pairBtn.addEventListener("click", () => run(() => bluetoothDeviceAction("pair", id)));
        actions.append(pairBtn);
      }

      if (!connected) {
        const connectBtn = document.createElement("button");
        connectBtn.className = "btn btn-outline-success btn-sm";
        connectBtn.textContent = "Verbinden";
        connectBtn.addEventListener("click", () => run(() => bluetoothDeviceAction("connect", id)));
        actions.append(connectBtn);
      } else {
        const disconnectBtn = document.createElement("button");
        disconnectBtn.className = "btn btn-outline-warning btn-sm";
        disconnectBtn.textContent = "Trennen";
        disconnectBtn.addEventListener("click", () => run(() => bluetoothDeviceAction("disconnect", id)));
        actions.append(disconnectBtn);
      }

      if (audioCapable) {
        const useOutBtn = document.createElement("button");
        useOutBtn.className = "btn btn-outline-secondary btn-sm";
        useOutBtn.textContent = "Als Output";
        useOutBtn.addEventListener("click", () => run(() => setAudioOutput(`bluetooth:${id}`)));
        actions.append(useOutBtn);
      }

      if (source !== "scan") {
        const forgetBtn = document.createElement("button");
        forgetBtn.className = "btn btn-outline-danger btn-sm";
        forgetBtn.textContent = "Forget";
        forgetBtn.addEventListener("click", () => run(() => bluetoothDeviceAction("forget", id)));
        actions.append(forgetBtn);
      }

      row.append(top, actions);
      host.append(row);
    }
  }

  function renderAudioOutputs(payload) {
    const data = payload && payload.data ? payload.data : payload || {};
    btDeviceState.audioOutputs = {
      current_output: String(data.current_output || ""),
      saved: (data.saved && typeof data.saved === "object") ? data.saved : {},
      available_outputs: Array.isArray(data.available_outputs) ? data.available_outputs : [],
    };
    const current = String(data.current_output || "-");
    const saved = String(((data.saved || {}).selected_output) || "-");
    q("bt-audio-output-current").textContent = current || "-";
    q("bt-audio-output-saved").textContent = saved || "-";
    const select = q("bt-audio-output-select");
    if (!select) return;
    select.innerHTML = "";
    const outputs = Array.isArray(data.available_outputs) ? data.available_outputs : [];
    if (!outputs.length) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "Keine Outputs gefunden";
      select.append(opt);
      return;
    }
    for (const item of outputs) {
      const id = String(item.id || "");
      const label = String(item.label || id || "Output");
      const available = !!item.available;
      const connected = item.connected === undefined ? null : !!item.connected;
      const status = available ? (connected === null ? "verfügbar" : (connected ? "connected" : "ready")) : "nicht verfügbar";
      const opt = document.createElement("option");
      opt.value = id;
      opt.disabled = !available;
      opt.textContent = `${label} (${status})`;
      if (id && id === data.current_output) {
        opt.selected = true;
      }
      select.append(opt);
    }
  }

  async function refreshBluetoothDevices() {
    const payload = await fetchJson("/api/bluetooth/devices", { cache: "no-store" });
    const data = payload.data || payload;
    const devices = Array.isArray(data.devices) ? data.devices : [];
    btDeviceState.devices = devices;
    renderBluetoothDeviceRows("bt-known-devices-list", devices, "known");
    return payload;
  }

  async function refreshBluetoothScan() {
    const durationRaw = Number(q("bt-scan-duration")?.value || 8);
    const duration = Number.isFinite(durationRaw) ? Math.max(4, Math.min(30, Math.round(durationRaw))) : 8;
    const payload = await fetchJson(`/api/bluetooth/scan?duration=${duration}`, { cache: "no-store" });
    const data = payload.data || payload;
    const devices = Array.isArray(data.devices) ? data.devices : [];
    btDeviceState.scanDevices = devices;
    renderBluetoothDeviceRows("bt-scan-results", devices, "scan");
    toast(`Bluetooth-Scan abgeschlossen (${devices.length} Geräte)`, "success");
    return payload;
  }

  async function bluetoothDeviceAction(action, deviceId) {
    if (!deviceId) throw new Error("device_id fehlt.");
    await fetchJson(`/api/bluetooth/${action}`, {
      method: "POST",
      body: { device_id: deviceId },
      timeoutMs: 45000,
    });
    await refreshNetwork();
    await refreshBluetoothDevices();
    await refreshAudioOutputs();
    toast(`Bluetooth ${action}: ${deviceId}`, "success");
  }

  async function refreshAudioOutputs() {
    const warmupData = consumeWarmupData(["sections", "legacy", "audio_outputs"]);
    if (warmupData && typeof warmupData === "object") {
      renderAudioOutputs(warmupData);
      return warmupData;
    }
    const payload = await fetchJson("/api/audio/outputs", { cache: "no-store" });
    renderAudioOutputs(payload);
    return payload;
  }

  async function setAudioOutput(outputId = "") {
    const selected = String(outputId || q("bt-audio-output-select")?.value || "").trim();
    if (!selected) throw new Error("Bitte Audio-Output auswählen.");
    await fetchJson("/api/audio/output", {
      method: "POST",
      body: { output: selected },
      timeoutMs: 20000,
    });
    await refreshAudioOutputs();
    await refreshAudioMixer();
    await refreshBluetoothDevices();
    toast(`Audio-Output gesetzt: ${selected}`, "success");
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
    const webhookModeInput = q("sentinel-webhook-mode");
    const webhookInput = q("sentinel-webhook-url");
    const internalWebhookInput = q("sentinel-internal-webhook-url");
    const internalWebhookSecretInput = q("sentinel-internal-webhook-secret");
    if (webhookModeInput && document.activeElement !== webhookModeInput) {
      webhookModeInput.value = String(state.webhookMode || "discord");
    }
    if (webhookInput && document.activeElement !== webhookInput) {
      webhookInput.value = String(state.webhookUrl || "");
    }
    if (internalWebhookInput && document.activeElement !== internalWebhookInput) {
      internalWebhookInput.value = String(state.internalWebhookUrl || "");
    }
    if (internalWebhookSecretInput && document.activeElement !== internalWebhookSecretInput) {
      internalWebhookSecretInput.value = String(state.internalWebhookSecret || "");
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
    const mode = String((webhookModeInput && webhookModeInput.value) || state.webhookMode || "discord").toLowerCase();
    if (webhookInput) {
      webhookInput.placeholder = mode === "internal" ? "Discord optional (nur bei Mode=both/discord)" : "https://discord.com/api/webhooks/...";
    }
    if (internalWebhookInput) {
      internalWebhookInput.placeholder = mode === "discord" ? "Incoming optional (nur bei Mode=both/internal)" : "https://joormann-family.de/api/webhooks/incoming/...";
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

      const runActions = document.createElement("div");
      runActions.className = "d-flex gap-1 flex-wrap justify-content-end";

      const startBtn = document.createElement("button");
      startBtn.type = "button";
      startBtn.className = "btn btn-outline-success btn-sm";
      startBtn.textContent = "Start";
      startBtn.dataset.action = "start";
      startBtn.dataset.slug = String(item.slug || "");
      startBtn.disabled = !item.installed;

      const stopBtn = document.createElement("button");
      stopBtn.type = "button";
      stopBtn.className = "btn btn-outline-warning btn-sm";
      stopBtn.textContent = "Stop";
      stopBtn.dataset.action = "stop";
      stopBtn.dataset.slug = String(item.slug || "");
      stopBtn.disabled = !item.installed;

      const restartBtn = document.createElement("button");
      restartBtn.type = "button";
      restartBtn.className = "btn btn-outline-primary btn-sm";
      restartBtn.textContent = "Restart";
      restartBtn.dataset.action = "restart";
      restartBtn.dataset.slug = String(item.slug || "");
      restartBtn.disabled = !item.installed;

      const testBtn = document.createElement("button");
      testBtn.type = "button";
      testBtn.className = "btn btn-outline-dark btn-sm";
      testBtn.textContent = "Test";
      testBtn.dataset.action = "test";
      testBtn.dataset.slug = String(item.slug || "");
      testBtn.disabled = !item.installed;

      runActions.append(startBtn, stopBtn, restartBtn, testBtn);
      right.append(badge, installBtn, uninstallBtn, runActions);
      row.append(left, right);
      listEl.appendChild(row);
    }
  }

  function applySentinelPayload(data) {
    const payload = data || {};
    portalSecurityState.sentinels.webhookMode = String(payload.webhook_mode || "discord");
    portalSecurityState.sentinels.webhookUrl = String(payload.webhook_url || "");
    portalSecurityState.sentinels.internalWebhookUrl = String(payload.internal_webhook_url || "");
    portalSecurityState.sentinels.internalWebhookSecret = String(payload.internal_webhook_secret || "");
    portalSecurityState.sentinels.sourceDir = String(payload.source_dir || "");
    portalSecurityState.sentinels.sourceError = String(payload.source_error || "");
    portalSecurityState.sentinels.configPath = String(payload.config_path || "");
    portalSecurityState.sentinels.items = Array.isArray(payload.sentinels) ? payload.sentinels : [];
    renderSentinelsUi();
  }

  async function refreshSentinelStatus(options = {}) {
    const notify = !(options && options.notify === false);
    const warmupData = consumeWarmupData(["sections", "legacy", "sentinels_status"]);
    const warmupHasSentinels =
      !!warmupData &&
      typeof warmupData === "object" &&
      Array.isArray(warmupData.sentinels);
    if (warmupHasSentinels) {
      applySentinelPayload(warmupData);
      return { ok: true, data: warmupData, source: "runtime_warmup" };
    }
    const payload = await fetchJson("/api/network/security/sentinels/status", { cache: "no-store", timeoutMs: 12000 });
    applySentinelPayload(payload.data || {});
    if (notify) {
      toast("Sentinel-Status aktualisiert.", "success");
    }
    return payload;
  }

  async function saveSentinelWebhook() {
    const webhookMode = String(q("sentinel-webhook-mode")?.value || "discord").trim().toLowerCase();
    const webhookUrl = String(q("sentinel-webhook-url")?.value || "").trim();
    const internalWebhookUrl = String(q("sentinel-internal-webhook-url")?.value || "").trim();
    const internalWebhookSecret = String(q("sentinel-internal-webhook-secret")?.value || "").trim();
    if (!webhookUrl && !internalWebhookUrl) {
      throw new Error("Bitte mindestens eine Webhook URL eintragen.");
    }
    const payload = await fetchJson("/api/network/security/sentinels/webhook", {
      method: "POST",
      body: {
        webhook_url: webhookUrl,
        internal_webhook_url: internalWebhookUrl,
        internal_webhook_secret: internalWebhookSecret,
        webhook_mode: webhookMode,
      },
      timeoutMs: 12000,
    });
    applySentinelPayload(payload.data || {});
    toast(payload.message || "Webhook URLs gespeichert.", "success");
  }

  async function testSentinelWebhook(target) {
    const normalized = String(target || "").trim().toLowerCase();
    if (normalized !== "discord" && normalized !== "internal") {
      throw new Error("Ungültiges Webhook-Testziel.");
    }
    const payload = await fetchJson("/api/network/security/sentinels/webhook/test", {
      method: "POST",
      body: { target: normalized },
      timeoutMs: 15000,
    });
    const label = normalized === "discord" ? "Discord" : "Incoming";
    toast(payload.message || `${label}-Webhook erfolgreich getestet.`, "success");
  }

  async function actionSentinel(slug, action) {
    const payload = await fetchJson("/api/network/security/sentinels/action", {
      method: "POST",
      body: { slug, action },
      timeoutMs: 45000,
    });
    applySentinelPayload(payload.data || {});
    toast(payload.message || `Sentinel ${slug}: ${action}`, "success");
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
        timeoutMs: 15000,
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
      timeoutMs: 45000,
    });

    const modalEl = q("hostnameRenameModal");
    const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    modal.hide();

    try {
      await refreshStatus();
    } catch (statusErr) {
      toast(`Hostname geändert, Status-Refresh fehlgeschlagen: ${statusErr.message || statusErr}`, "warning");
    }
    try {
      await refreshNetwork();
      await refreshApStatus();
    } catch (netErr) {
      toast(`Hostname geändert, Netzwerk-Refresh fehlgeschlagen: ${netErr.message || netErr}`, "warning");
    }
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
    const logEl = getUpdateLogEl();
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Update läuft...';
    openUpdateModal("Portal-Update");
    _pendingHistoryMeta = { kind: "portal", name: "Device Portal", service_name: "device-portal.service" };
    try {
      const payload = await fetchJson("/api/system/portal/update", { method: "POST", timeoutMs: 30000 });
      const data = payload.data || {};
      currentUpdateJobId = String(data.job_id || "").trim();
      if (currentUpdateJobId) {
        await pollPortalUpdateStatus(currentUpdateJobId, true);
      } else {
        logEl.textContent = "Update wurde gestartet, aber keine Job-ID wurde zurückgegeben.";
      }
      await refreshSystemUpdateSummaries();
      toast(data.message || "Update ausgelöst. Service wird neu gestartet.", "success");
    } finally {
      btn.disabled = false;
      btn.innerHTML = original;
    }
  }

  async function installPortalService() {
    const btn = q("btn-system-install-portal-service");
    const logEl = getUpdateLogEl();
    if (!btn || !logEl) return;
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Installiere...';
    try {
      const payload = await fetchJson("/api/system/portal/service/install", {
        method: "POST",
        timeoutMs: 60000,
      });
      const data = payload.data || {};
      const lines = [
        `status: ${data.active_state || "-"}/${data.substate || "-"}`,
        `message: ${data.message || "-"}`,
        `repo: ${data.repo_dir || "-"}`,
        `service: ${data.service_name || "-"}`,
        `user: ${data.service_user || "-"}`,
        `working_directory: ${data.working_directory || "-"}`,
        `exec_start: ${data.exec_start || "-"}`,
        `fragment_path: ${data.fragment_path || "-"}`,
      ];
      logEl.textContent = lines.join("\n");
      toast(data.message || "Portal-Service installiert/umgeschaltet.", "success");
      await refreshStatus();
      await refreshSystemUpdateSummaries();
    } finally {
      btn.disabled = false;
      btn.innerHTML = original;
    }
  }

  function renderPortalUpdateStatus(data) {
    const logEl = getUpdateLogEl();
    const shouldStickBottom = isNearBottom(logEl);
    const status = String(data.status || "unknown");
    const success = !!data.success;
    const lines = [
      `status: ${status}`,
      `success: ${String(success)}`,
      `message: ${data.message || "-"}`,
      `job_id: ${data.job_id || "-"}`,
      `repo: ${data.repo_dir || "-"}`,
      `repo_link: ${data.repo_link || "-"}`,
      `install_dir: ${data.install_dir || "-"}`,
      `user: ${data.service_user || "-"}`,
      `service: ${data.service_name || "-"}`,
      `use_service: ${String(data.use_service ?? true)}`,
      `autostart: ${String(data.autostart ?? true)}`,
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
    if (shouldStickBottom) {
      logEl.scrollTop = logEl.scrollHeight;
    }
    try {
      window.localStorage.setItem(UPDATE_CACHE_KEY, JSON.stringify({ data, cached_at: new Date().toISOString() }));
    } catch (_) {
      // ignore cache errors
    }
    if (_updateModalActive || status === "done" || status === "failed") {
      _updateModalLog(data.log || lines.join("\n"));
      _setUpdateModalStatus(status, !!success);
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
          const meta = _pendingHistoryMeta;
          _pendingHistoryMeta = null;
          if (meta) {
            recordUpdateHistory({
              kind: meta.kind || "portal",
              name: meta.name || "Device Portal",
              status,
              success: !!data.success,
              before_commit: data.before_commit || "",
              after_commit: data.after_commit || "",
              service_name: meta.service_name || data.service_name || "",
              ts: new Date().toISOString(),
            });
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
    const systemUpdateTab = q("system-update-tab");
    const updatePane = q("system-update-pane");
    if (systemUpdateTab && window.bootstrap && window.bootstrap.Tab) {
      window.bootstrap.Tab.getOrCreateInstance(systemUpdateTab).show();
    } else if (systemUpdateTab) {
      systemUpdateTab.click();
    }
    if (updatePane && typeof updatePane.scrollIntoView === "function") {
      updatePane.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  function bindButtons() {
    const updateMainTab = q("system-update-tab");
    if (updateMainTab) {
      const refreshUpdateTabData = () => {
        runQuiet(refreshManagedRepos);
        runQuiet(refreshAutodiscoverServices);
        runQuiet(refreshStatus);
        runQuiet(fetchAndRenderUpdateHistory);
      };
      updateMainTab.addEventListener("click", refreshUpdateTabData);
      updateMainTab.addEventListener("shown.bs.tab", refreshUpdateTabData);
    }

    const clearHistoryBtn = q("btn-clear-update-history");
    if (clearHistoryBtn) {
      clearHistoryBtn.addEventListener("click", () => run(async () => {
        await fetchJson("/api/update-history", { method: "DELETE", timeoutMs: 5000 });
        renderUpdateHistory([]);
        toast("Verlauf gelöscht", "success");
      }));
    }

    const modalCloseBtn = document.getElementById("update-modal-close-btn");
    const modalCloseX = document.getElementById("update-modal-close-x");
    const closeModal = () => {
      const modal = _getUpdateModal();
      if (modal && !_updateModalActive) modal.hide();
    };
    if (modalCloseBtn) modalCloseBtn.addEventListener("click", closeModal);
    if (modalCloseX) modalCloseX.addEventListener("click", closeModal);

    const checkRepoUpdatesBtn = q("btn-check-repo-updates");
    if (checkRepoUpdatesBtn) {
      checkRepoUpdatesBtn.addEventListener("click", () => run(async () => {
        const origHtml = checkRepoUpdatesBtn.innerHTML;
        checkRepoUpdatesBtn.disabled = true;
        checkRepoUpdatesBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span>Prüfe...';
        try {
          const data = await fetchJson("/api/status/check-updates", { method: "POST", cache: "no-store" });
          renderStatus(data);
          runQuiet(refreshManagedRepos);
          toast("Update-Prüfung abgeschlossen", "success");
        } finally {
          checkRepoUpdatesBtn.disabled = false;
          checkRepoUpdatesBtn.innerHTML = origHtml;
        }
      }));
    }

    initPlayerRepoFieldSync();
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
  const clearScreenshotsBtn = q("btn-display-screenshots-clear");
  if (clearScreenshotsBtn) {
    clearScreenshotsBtn.addEventListener("click", () => {
      run(async () => {
        await fetchJson("/api/display/screenshots/clear", { method: "POST" });
        await refreshStatus();
        toast("Screenshots gelöscht", "success");
      });
    });
  }
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
    q("btn-player-service-install").addEventListener("click", () => run(installPlayerService));
    q("btn-spotify-connect-start").addEventListener("click", () => run(() => spotifyConnectAction("start")));
    q("btn-spotify-connect-stop").addEventListener("click", () => run(() => spotifyConnectAction("stop")));
    q("btn-spotify-connect-restart").addEventListener("click", () => run(() => spotifyConnectAction("restart")));
    q("btn-spotify-connect-refresh").addEventListener("click", () => run(refreshSpotifyConnectStatus));
    q("btn-spotify-connect-install").addEventListener("click", () => run(() => spotifyConnectAction("install")));
    q("btn-spotify-connect-enable").addEventListener("click", () => run(() => spotifyConnectAction("enable")));
    q("btn-spotify-connect-disable").addEventListener("click", () => run(() => spotifyConnectAction("disable")));
    q("btn-spotify-connect-config-save").addEventListener("click", () => run(saveSpotifyConnectConfig));
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
      syncMasterAndCurrentChannelUi(clamped);
      scheduleStreamAudioVolumeSet();
    });
    q("stream-audio-volume").addEventListener("change", () => run(() => streamAudioSetVolume({ notify: false, refresh: true })));
    q("stream-audio-file-select").addEventListener("change", () => {
      const selected = String(q("stream-audio-file-select")?.value || "").trim();
      if (selected) q("stream-audio-file-path").value = selected;
    });
    const streamAudioBrowseBtn = q("btn-stream-audio-file-browse");
    if (streamAudioBrowseBtn) {
      streamAudioBrowseBtn.addEventListener("click", () => run(openStreamAudioPathModal));
    }
    const streamAudioFilesLoadBtn = q("btn-stream-audio-files-load");
    if (streamAudioFilesLoadBtn) {
      streamAudioFilesLoadBtn.addEventListener("click", () => run(() => refreshStreamAudioFiles()));
    }
    const streamPlayerRepoSaveBtn = q("btn-stream-player-repo-save");
    if (streamPlayerRepoSaveBtn) {
      streamPlayerRepoSaveBtn.addEventListener("click", () => run(savePlayerRepoConfig));
    }
    q("btn-stream-player-install-update").addEventListener("click", () => run(startStreamPlayerInstallUpdate));
    q("btn-stream-player-update-status").addEventListener("click", () => run(() => pollStreamPlayerUpdateStatus(streamPlayerUpdateJobId)));
    const btnSystemPlayerUpdate = q("btn-system-player-update");
    if (btnSystemPlayerUpdate) {
      btnSystemPlayerUpdate.addEventListener("click", () => run(startStreamPlayerInstallUpdate));
    }
    const extraRepoSaveBtn = q("btn-extra-repo-save");
    if (extraRepoSaveBtn) {
      extraRepoSaveBtn.addEventListener("click", () => run(saveManagedRepo));
    }
    const extraRepoLinkInput = q("extra-repo-link");
    if (extraRepoLinkInput) {
      extraRepoLinkInput.addEventListener("change", syncManagedRepoDerivedInstallDir);
      extraRepoLinkInput.addEventListener("blur", syncManagedRepoDerivedInstallDir);
    }
    const extraRepoUserInput = q("extra-repo-service-user");
    if (extraRepoUserInput) {
      extraRepoUserInput.addEventListener("change", syncManagedRepoDerivedInstallDir);
      extraRepoUserInput.addEventListener("blur", syncManagedRepoDerivedInstallDir);
    }
    const extraRepoRefreshBtn = q("btn-extra-repo-refresh");
    if (extraRepoRefreshBtn) {
      extraRepoRefreshBtn.addEventListener("click", () => run(() => refreshManagedRepos(true)));
    }
    const extraRepoBrowseBtn = q("btn-extra-repo-install-dir-browse");
    if (extraRepoBrowseBtn) {
      extraRepoBrowseBtn.addEventListener("click", () => run(openRepoInstallPathModal));
    }
    const bulkSelectAll = q("bulk-select-all-updates");
    if (bulkSelectAll) {
      bulkSelectAll.addEventListener("change", () => {
        const checks = document.querySelectorAll(".js-repo-update-check:not(:disabled)");
        checks.forEach((c) => { c.checked = bulkSelectAll.checked; });
        updateBulkUpdateToolbar();
      });
    }
    const bulkUpdateBtn = q("btn-bulk-update-repos");
    if (bulkUpdateBtn) {
      bulkUpdateBtn.addEventListener("click", () => run(bulkUpdateSelectedRepos));
    }

    // Also update toolbar when the fixed portal/player checkboxes change
    ["repo-upd-chk-__portal__", "repo-upd-chk-__display_player__"].forEach((cbId) => {
      const cb = document.getElementById(cbId);
      if (cb) cb.addEventListener("change", updateBulkUpdateToolbar);
    });

    const extraRepoList = q("extra-repos-list");
    if (extraRepoList) {
      extraRepoList.addEventListener("change", (event) => {
        if (event.target && event.target.classList.contains("js-repo-update-check")) {
          updateBulkUpdateToolbar();
        }
      });
      extraRepoList.addEventListener("click", (event) => {
        const btn = event.target && event.target.closest ? event.target.closest("button.js-extra-repo-action") : null;
        if (!btn) return;
        const action = String(btn.dataset.action || "").trim();
        const repoId = String(btn.dataset.id || "").trim();
        if (!action || !repoId) return;
        run(async () => {
          if (action === "delete") {
            await deleteManagedRepo(repoId);
            return;
          }
          if (action === "update") {
            await startManagedRepoInstallUpdate(repoId);
            await refreshStatus();
            return;
          }
          if (action === "install_update") {
            await startManagedRepoInstallUpdate(repoId);
            await refreshStatus();
            return;
          }
          if (action === "details") {
            showManagedRepoDetails(repoId);
            return;
          }
          if (action === "edit") {
            openRepoEditModal(repoId);
            return;
          }
          if (action === "change_path") {
            openRepoChangePathModal(repoId);
            return;
          }
          if (action === "reinstall") {
            openRepoReinstallModal(repoId);
            return;
          }
          if (action === "toggle_autostart") {
            const enabled = String(btn.dataset.enabled || "").trim() === "1";
            await toggleManagedRepoAutostart(repoId, enabled);
            return;
          }
          if (action === "service_action") {
            const serviceAction = String(btn.dataset.serviceAction || "").trim().toLowerCase();
            await controlManagedRepoService(repoId, serviceAction);
            return;
          }
          if (action === "uninstall") {
            await uninstallManagedRepo(repoId);
          }
        });
      });
    }
    const llmDetailsBtn = q("btn-llm-manager-details");
    if (llmDetailsBtn) {
      llmDetailsBtn.addEventListener("click", () => run(async () => {
        const repoId = String(llmManagerState?.repo?.id || "").trim();
        if (!repoId) throw new Error("LLM-Manager Repo nicht gefunden.");
        showManagedRepoDetails(repoId);
      }));
    }
    const llmUpdateBtn = q("btn-llm-manager-update");
    if (llmUpdateBtn) {
      llmUpdateBtn.addEventListener("click", () => run(async () => {
        const repoId = String(llmManagerState?.repo?.id || "").trim();
        if (!repoId) throw new Error("LLM-Manager Repo nicht gefunden.");
        await startManagedRepoInstallUpdate(repoId);
        await refreshStatus();
      }));
    }
    const llmReportBtn = q("btn-llm-manager-report");
    if (llmReportBtn) {
      llmReportBtn.addEventListener("click", () => run(async () => {
        await fetchJson("/api/llm-manager/refresh", { method: "POST" });
        await refreshLlmManagerInfo();
        await refreshStatus();
        toast("LLM-Manager aktualisiert", "success");
      }));
    }
    const llmToggleBtn = q("btn-llm-manager-toggle");
    if (llmToggleBtn) {
      llmToggleBtn.addEventListener("click", () => run(async () => {
        const repoId = String(llmManagerState?.repo?.id || "").trim();
        const action = String(llmToggleBtn.dataset.action || "").trim().toLowerCase();
        if (!repoId) throw new Error("LLM-Manager Repo nicht gefunden.");
        if (!action) throw new Error("Ungültige Action.");
        await controlManagedRepoService(repoId, action);
        if (action === "start") {
          await new Promise((resolve) => window.setTimeout(resolve, 1200));
          await fetchJson("/api/llm-manager/refresh", { method: "POST" });
          await refreshStatus();
        }
      }));
    }
    const llmRestartBtn = q("btn-llm-manager-restart");
    if (llmRestartBtn) {
      llmRestartBtn.addEventListener("click", () => run(async () => {
        const repoId = String(llmManagerState?.repo?.id || "").trim();
        if (!repoId) throw new Error("LLM-Manager Repo nicht gefunden.");
        await controlManagedRepoService(repoId, "restart");
        await new Promise((resolve) => window.setTimeout(resolve, 1200));
        await fetchJson("/api/llm-manager/refresh", { method: "POST" });
        await refreshStatus();
      }));
    }
    const llmAutostartBtn = q("btn-llm-manager-autostart");
    if (llmAutostartBtn) {
      llmAutostartBtn.addEventListener("click", () => run(async () => {
        const repoId = String(llmManagerState?.repo?.id || "").trim();
        const enabled = String(llmAutostartBtn.dataset.enabled || "").trim() === "1";
        if (!repoId) throw new Error("LLM-Manager Repo nicht gefunden.");
        await toggleManagedRepoAutostart(repoId, enabled);
      }));
    }
    const autodiscoverRefreshBtn = q("btn-autodiscover-refresh");
    if (autodiscoverRefreshBtn) {
      autodiscoverRefreshBtn.addEventListener("click", () => run(refreshAutodiscoverServices));
    }
    const autodiscoverList = q("autodiscover-services-list");
    if (autodiscoverList) {
      autodiscoverList.addEventListener("click", (event) => {
        const btn = event.target && event.target.closest ? event.target.closest("button.js-autodiscover-action") : null;
        if (!btn) return;
        const action = String(btn.dataset.action || "").trim();
        const serviceId = String(btn.dataset.id || "").trim();
        if (!action || !serviceId) return;
        run(async () => {
          if (action === "promote") {
            await promoteAutodiscoveredService(serviceId);
            await refreshAutodiscoverServices();
          }
        });
      });
    }
    const repoInstallPathList = q("repo-install-path-list");
    if (repoInstallPathList) {
      repoInstallPathList.addEventListener("click", (event) => {
        const btn = event.target && event.target.closest ? event.target.closest("button.js-repo-path-entry") : null;
        if (!btn) return;
        const path = String(btn.dataset.path || "").trim();
        if (!path) return;
        run(() => refreshRepoInstallPathBrowser(path));
      });
    }
    const repoInstallPathRoots = q("repo-install-path-roots");
    if (repoInstallPathRoots) {
      repoInstallPathRoots.addEventListener("click", (event) => {
        const btn = event.target && event.target.closest ? event.target.closest("button.js-repo-path-root") : null;
        if (!btn) return;
        const path = String(btn.dataset.path || "").trim();
        if (!path) return;
        run(() => refreshRepoInstallPathBrowser(path));
      });
    }
    const repoInstallPathUpBtn = q("btn-repo-install-path-up");
    if (repoInstallPathUpBtn) {
      repoInstallPathUpBtn.addEventListener("click", () => run(async () => {
        const path = String(repoInstallPathState.parentPath || "").trim();
        if (!path) return;
        await refreshRepoInstallPathBrowser(path);
      }));
    }
    const repoInstallPathHomeBtn = q("btn-repo-install-path-home");
    if (repoInstallPathHomeBtn) {
      repoInstallPathHomeBtn.addEventListener("click", () => run(async () => {
        const roots = Array.isArray(repoInstallPathState.roots) ? repoInstallPathState.roots : [];
        const target = String(roots[0] || "").trim();
        await refreshRepoInstallPathBrowser(target);
      }));
    }
    const repoInstallPathRefreshBtn = q("btn-repo-install-path-refresh");
    if (repoInstallPathRefreshBtn) {
      repoInstallPathRefreshBtn.addEventListener("click", () => run(async () => {
        await refreshRepoInstallPathBrowser(repoInstallPathState.currentPath || "");
      }));
    }
    const repoInstallPathApplyBtn = q("btn-repo-install-path-apply");
    if (repoInstallPathApplyBtn) {
      repoInstallPathApplyBtn.addEventListener("click", () => {
        const targetId = repoInstallPathState.targetInputId || "extra-repo-install-dir";
        const input = q(targetId);
        if (input) input.value = String(repoInstallPathState.currentPath || "").trim();
        const modalEl = q("repoInstallPathModal");
        if (modalEl && window.bootstrap && window.bootstrap.Modal) {
          window.bootstrap.Modal.getOrCreateInstance(modalEl).hide();
        }
      });
    }
    const changePathBrowseBtn = q("btn-change-path-browse");
    if (changePathBrowseBtn) {
      changePathBrowseBtn.addEventListener("click", () => run(async () => {
        const changePathModal = q("repoChangePathModal");
        if (changePathModal && window.bootstrap && window.bootstrap.Modal) {
          window.bootstrap.Modal.getOrCreateInstance(changePathModal).hide();
        }
        await openRepoInstallPathModal("change-path-install-dir");
      }));
    }
    const changePathSaveBtn = q("btn-change-path-save");
    if (changePathSaveBtn) {
      changePathSaveBtn.addEventListener("click", () => run(async () => {
        const repoId = String(q("change-path-repo-id")?.value || _repoChangePathRepoId || "").trim();
        const newPath = String(q("change-path-install-dir")?.value || "").trim();
        if (!repoId) throw new Error("Repo-ID fehlt.");
        if (!newPath) throw new Error("Bitte einen Pfad eingeben.");
        await saveRepoPath(repoId, newPath, false);
      }));
    }
    const changePathSaveReinstallBtn = q("btn-change-path-save-reinstall");
    if (changePathSaveReinstallBtn) {
      changePathSaveReinstallBtn.addEventListener("click", () => run(async () => {
        const repoId = String(q("change-path-repo-id")?.value || _repoChangePathRepoId || "").trim();
        const newPath = String(q("change-path-install-dir")?.value || "").trim();
        if (!repoId) throw new Error("Repo-ID fehlt.");
        if (!newPath) throw new Error("Bitte einen Pfad eingeben.");
        await saveRepoPath(repoId, newPath, true);
      }));
    }
    const repoEditChangePathBtn = q("btn-repo-edit-change-path");
    if (repoEditChangePathBtn) {
      repoEditChangePathBtn.addEventListener("click", () => run(() => {
        const repoId = String(q("repo-edit-modal-repo-id")?.value || repoEditChangePathBtn.dataset.id || "").trim();
        _closeEditModal();
        openRepoChangePathModal(repoId);
      }));
    }
    const repoEditReinstallBtn = q("btn-repo-edit-reinstall");
    if (repoEditReinstallBtn) {
      repoEditReinstallBtn.addEventListener("click", () => run(() => {
        const repoId = String(q("repo-edit-modal-repo-id")?.value || repoEditReinstallBtn.dataset.id || "").trim();
        _closeEditModal();
        openRepoReinstallModal(repoId);
      }));
    }
    const repoEditUpdateBtn = q("btn-repo-edit-update");
    if (repoEditUpdateBtn) {
      repoEditUpdateBtn.addEventListener("click", () => run(async () => {
        const repoId = String(q("repo-edit-modal-repo-id")?.value || repoEditUpdateBtn.dataset.id || "").trim();
        _closeEditModal();
        await startManagedRepoInstallUpdate(repoId);
        await refreshStatus();
      }));
    }
    const repoEditAutostartBtn = q("btn-repo-edit-autostart");
    if (repoEditAutostartBtn) {
      repoEditAutostartBtn.addEventListener("click", () => run(async () => {
        const repoId = String(q("repo-edit-modal-repo-id")?.value || repoEditAutostartBtn.dataset.id || "").trim();
        const enabled = String(repoEditAutostartBtn.dataset.enabled || "").trim() === "1";
        _closeEditModal();
        await toggleManagedRepoAutostart(repoId, enabled);
      }));
    }
    const repoEditUninstallBtn = q("btn-repo-edit-uninstall");
    if (repoEditUninstallBtn) {
      repoEditUninstallBtn.addEventListener("click", () => run(async () => {
        const repoId = String(q("repo-edit-modal-repo-id")?.value || repoEditUninstallBtn.dataset.id || "").trim();
        const action = String(repoEditUninstallBtn.dataset.action || "uninstall").trim();
        _closeEditModal();
        if (action === "install") {
          await startManagedRepoInstallUpdate(repoId);
          await refreshStatus();
        } else {
          await uninstallManagedRepo(repoId);
        }
      }));
    }
    const repoEditDeleteBtn = q("btn-repo-edit-delete");
    if (repoEditDeleteBtn) {
      repoEditDeleteBtn.addEventListener("click", () => run(async () => {
        const repoId = String(q("repo-edit-modal-repo-id")?.value || repoEditDeleteBtn.dataset.id || "").trim();
        _closeEditModal();
        await deleteManagedRepo(repoId);
      }));
    }
    const openBasePathModalBtn = q("btn-open-base-path-modal");
    if (openBasePathModalBtn) {
      openBasePathModalBtn.addEventListener("click", () => run(openRepoBasePathModal));
    }
    const basePathBrowseBtn = q("btn-base-path-browse");
    if (basePathBrowseBtn) {
      basePathBrowseBtn.addEventListener("click", () => run(async () => {
        const baseModal = q("repoBasePathModal");
        if (baseModal && window.bootstrap && window.bootstrap.Modal) {
          window.bootstrap.Modal.getOrCreateInstance(baseModal).hide();
        }
        await openRepoInstallPathModal("base-path-input");
      }));
    }
    const basePathPreviewBtn = q("btn-base-path-preview");
    if (basePathPreviewBtn) {
      basePathPreviewBtn.addEventListener("click", () => run(loadBasePathPreview));
    }
    const basePathSaveBtn = q("btn-base-path-save");
    if (basePathSaveBtn) {
      basePathSaveBtn.addEventListener("click", () => run(() => saveBasePath(false)));
    }
    const basePathSaveReinstallBtn = q("btn-base-path-save-reinstall");
    if (basePathSaveReinstallBtn) {
      basePathSaveReinstallBtn.addEventListener("click", () => run(() => saveBasePath(true)));
    }
    const reinstallBrowseBtn = q("btn-reinstall-browse");
    if (reinstallBrowseBtn) {
      reinstallBrowseBtn.addEventListener("click", () => run(async () => {
        const reinstallModal = q("repoReinstallModal");
        if (reinstallModal && window.bootstrap && window.bootstrap.Modal) {
          window.bootstrap.Modal.getOrCreateInstance(reinstallModal).hide();
        }
        await openRepoInstallPathModal("reinstall-install-dir");
      }));
    }
    const reinstallConfirmBtn = q("btn-reinstall-confirm");
    if (reinstallConfirmBtn) {
      reinstallConfirmBtn.addEventListener("click", () => run(async () => {
        const repoId = String(q("reinstall-repo-id")?.value || "").trim();
        await startRepoReinstall(repoId);
      }));
    }
    const reinstallDirInput = q("reinstall-install-dir");
    if (reinstallDirInput) {
      reinstallDirInput.addEventListener("input", () => {
        const hint = q("reinstall-path-changed-hint");
        if (!hint) return;
        const current = String(reinstallDirInput.value || "").trim();
        const original = String(reinstallDirInput._originalValue || "").trim();
        hint.classList.toggle("d-none", current === original);
      });
    }
    const streamAudioPathList = q("stream-audio-browser-list");
    if (streamAudioPathList) {
      streamAudioPathList.addEventListener("click", (event) => {
        const btn = event.target && event.target.closest ? event.target.closest("button.js-stream-audio-path-entry") : null;
        if (!btn) return;
        const path = String(btn.dataset.path || "").trim();
        const kind = String(btn.dataset.kind || "").trim();
        if (!path) return;
        run(async () => {
          if (kind === "file") {
            const input = q("stream-audio-file-path");
            if (input) input.value = path;
            const modalEl = q("streamAudioFileBrowserModal");
            if (modalEl && window.bootstrap && window.bootstrap.Modal) {
              window.bootstrap.Modal.getOrCreateInstance(modalEl).hide();
            }
            return;
          }
          await refreshStreamAudioPathBrowser(path);
        });
      });
    }
    const streamAudioPathUpBtn = q("btn-stream-audio-browser-up");
    if (streamAudioPathUpBtn) {
      streamAudioPathUpBtn.addEventListener("click", () => run(async () => {
        const path = String(streamAudioBrowserState.parentPath || "").trim();
        if (!path) return;
        await refreshStreamAudioPathBrowser(path);
      }));
    }
    const streamAudioPathRootBtn = q("btn-stream-audio-browser-root");
    if (streamAudioPathRootBtn) {
      streamAudioPathRootBtn.addEventListener("click", () => run(() => refreshStreamAudioPathBrowser(streamAudioBrowserState.rootPath || "/mnt")));
    }
    const streamAudioPathRefreshBtn = q("btn-stream-audio-browser-refresh");
    if (streamAudioPathRefreshBtn) {
      streamAudioPathRefreshBtn.addEventListener("click", () => run(() => refreshStreamAudioPathBrowser(streamAudioBrowserState.currentPath || "/mnt")));
    }
    const streamAudioPathApplyDirBtn = q("btn-stream-audio-browser-apply-dir");
    if (streamAudioPathApplyDirBtn) {
      streamAudioPathApplyDirBtn.addEventListener("click", () => {
        const chosenPath = String(streamAudioBrowserState.currentPath || "").trim();
        const input = q("stream-audio-file-path");
        if (input) input.value = chosenPath;
        const modalEl = q("streamAudioFileBrowserModal");
        if (modalEl && window.bootstrap && window.bootstrap.Modal) {
          window.bootstrap.Modal.getOrCreateInstance(modalEl).hide();
        }
        if (chosenPath) {
          run(() => refreshStreamAudioFiles(chosenPath));
        }
      });
    }
    const repoSaveQuick = q("btn-stream-player-repo-save-quick");
    if (repoSaveQuick) {
      repoSaveQuick.addEventListener("click", () => run(savePlayerRepoConfig));
    }
    const installUpdateQuick = q("btn-stream-player-install-update-quick");
    if (installUpdateQuick) {
      installUpdateQuick.addEventListener("click", () => run(startStreamPlayerInstallUpdate));
    }
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
    const softwareReqRefreshBtn = q("btn-software-req-refresh");
    if (softwareReqRefreshBtn) {
      softwareReqRefreshBtn.addEventListener("click", () => run(refreshSoftwareRequirements));
    }
    const softwareReqTable = q("software-req-table");
    if (softwareReqTable) {
      softwareReqTable.addEventListener("click", (event) => {
        const btn = event.target && event.target.closest ? event.target.closest("button.js-software-req-action") : null;
        if (!btn) return;
        const action = String(btn.dataset.action || "").trim();
        const key = String(btn.dataset.key || "").trim();
        if (!action || !key) return;
        run(async () => {
          btn.disabled = true;
          const old = btn.textContent;
          btn.textContent = "Bitte warten...";
          try {
            await runSoftwareRequirementAction(action, key);
          } finally {
            btn.disabled = false;
            btn.textContent = old;
          }
        });
      });
    }

    els.btnWifiToggle.addEventListener("click", () => run(toggleWifi));
    els.btnBtToggle.addEventListener("click", () => run(toggleBluetooth));
    els.btnBtPairingStart.addEventListener("click", () => run(startBluetoothPairing));
    q("btn-bt-scan").addEventListener("click", () => run(refreshBluetoothScan));
    q("btn-bt-devices-refresh").addEventListener("click", () => run(refreshBluetoothDevices));
    q("btn-audio-output-refresh").addEventListener("click", () => run(refreshAudioOutputs));
    q("btn-audio-output-set").addEventListener("click", () => run(() => setAudioOutput("")));
    q("btn-audio-status-refresh").addEventListener("click", () => run(refreshAudioHubStatus));
    q("btn-audio-mixer-refresh").addEventListener("click", () => run(refreshAudioMixer));
    q("btn-audio-ducking-save").addEventListener("click", () => run(saveAudioMixerSettings));
    q("audio-tts-volume").addEventListener("input", () => setAudioRangeLabel("audio-tts-volume", "audio-tts-volume-value"));
    q("audio-ducking-level").addEventListener("input", () => setAudioRangeLabel("audio-ducking-level", "audio-ducking-level-value"));
    q("audio-tts-target-mode").addEventListener("change", updateAudioTtsTargetUi);
    q("btn-audio-radio-play").addEventListener("click", () => run(audioRadioPlay));
    q("btn-audio-radio-stop").addEventListener("click", () => run(audioRadioStop));
    q("btn-audio-tts-test").addEventListener("click", () => run(audioTtsTest));
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
    q("btn-sentinel-test-discord").addEventListener("click", () => run(() => testSentinelWebhook("discord")));
    q("btn-sentinel-test-internal").addEventListener("click", () => run(() => testSentinelWebhook("internal")));
    q("sentinel-webhook-mode").addEventListener("change", () => renderSentinelsUi());
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
    const btnSystemPortalUpdate = q("btn-system-portal-update");
    if (btnSystemPortalUpdate) {
      btnSystemPortalUpdate.addEventListener("click", () => run(updatePortal));
    }
    q("btn-system-install-portal-service").addEventListener("click", () => run(installPortalService));
    const btnSystemPortalDetails = q("btn-system-portal-details");
    if (btnSystemPortalDetails) {
      btnSystemPortalDetails.addEventListener("click", () => showSystemUpdateDetails("portal"));
    }
    const btnPortalRestartNow = q("btn-system-portal-restart-now");
    if (btnPortalRestartNow) {
      btnPortalRestartNow.addEventListener("click", () => run(restartPortalServiceNow));
    }
    const btnSystemPortalToggle = q("btn-system-portal-toggle");
    if (btnSystemPortalToggle) {
      btnSystemPortalToggle.addEventListener("click", () => run(() => portalServiceAction(btnSystemPortalToggle.dataset.action || "start")));
    }
    const btnSystemPlayerToggle = q("btn-system-player-toggle");
    if (btnSystemPlayerToggle) {
      btnSystemPlayerToggle.addEventListener("click", () => run(() => playerAction(btnSystemPlayerToggle.dataset.action || "start")));
    }
    const btnSystemPlayerRestart = q("btn-system-player-restart");
    if (btnSystemPlayerRestart) {
      btnSystemPlayerRestart.addEventListener("click", () => run(() => playerAction("restart")));
    }
    const btnSystemPlayerDetails = q("btn-system-player-details");
    if (btnSystemPlayerDetails) {
      btnSystemPlayerDetails.addEventListener("click", () => showSystemUpdateDetails("player"));
    }
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
        return;
      }
      if (action === "start" || action === "stop" || action === "restart" || action === "test") {
        run(() => actionSentinel(slug, action));
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
      const message = err && err.message ? String(err.message) : String(err);
      const lowered = message.toLowerCase();
      if (lowered.includes("repo_not_found") || lowered.includes("repo nicht gefunden")) {
        await runQuiet(() => refreshManagedRepos(true));
      }
      toast(message, "danger");
    }
  }

  async function runQuiet(fn, suppressed = []) {
    try {
      await fn();
    } catch (err) {
      const msg = String((err && err.message) ? err.message : err || "").toLowerCase();
      if (suppressed.some((needle) => msg.includes(String(needle).toLowerCase()))) {
        return;
      }
      // Quiet mode for boot: keep console trace, avoid noisy startup toasts.
      try { console.warn("boot task failed:", err); } catch (_) {}
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
    setupSessionCloseLogout();
    bindButtons();
    applyStreamFeatureVisibility();
    // Do not render stale managed-repo cache before first server sync.
    try {
      const raw = window.localStorage.getItem(SYSTEM_UPDATE_SUMMARY_CACHE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === "object") {
          renderSystemUpdateSummary(parsed);
        }
      }
    } catch (_) {}
    runQuiet(async () => {
      const payload = await fetchJson("/api/system/update-summary", { timeoutMs: 8000 });
      const summary = (payload && payload.data && typeof payload.data === "object") ? payload.data : null;
      if (!summary) return;
      renderSystemUpdateSummary(summary);
      try {
        window.localStorage.setItem(SYSTEM_UPDATE_SUMMARY_CACHE_KEY, JSON.stringify(summary));
      } catch (_) {}
    });
    runQuiet(refreshManagedRepos);
    runQuiet(refreshAutodiscoverServices);
    await run(runRuntimeWarmupFlow);
    await run(refreshStatus);
    await run(primeAudioHubData);
    await run(refreshPanelFlagsLive);
    await run(refreshSyncStatus);
    await runQuiet(panelSyncCheck, ["device_not_linked", "setup_required"]);
    await run(refreshNetwork);
    await run(refreshWifiScan);
    await run(refreshWifiProfiles);
    await run(refreshWpsStatus);
    await run(refreshWifiLogs);
    await run(refreshStorageStatus);
    await run(refreshStreamOverview);
    await runQuiet(refreshSpotifyConnectStatus, ["spotify connect action failed", "sudo: ein passwort ist notwendig"]);
    await runQuiet(refreshSpotifyConnectConfig, ["spotify connect action failed", "sudo: ein passwort ist notwendig"]);
    await run(refreshStreamAudioFiles);
    await runQuiet(refreshStreamAudioStatus, ["audio_control_unreachable", "connection refused"]);
    await run(refreshBluetoothDevices);
    await runQuiet(primeAudioHubData, ["audio_control_unreachable", "connection refused"]);
    await run(loadPlayerRepoConfig);
    await run(refreshManagedRepos);
    await run(refreshAutodiscoverServices);
    await run(() => pollStreamPlayerUpdateStatus(""));
    await run(refreshApStatus);
    await run(refreshApClients);
    await run(() => refreshSentinelStatus({ notify: false }));
    await run(refreshSoftwareRequirements);
    await run(loadLastPortalUpdateStatus);
    await run(refreshOverlayState);
    runQuiet(fetchAndRenderUpdateHistory);
    flushPersistedUpdateResultFlash();
    startApPolling();
    startStoragePolling();
    startAudioHubPolling();
    setWpsTarget(null);
  }

  window.addEventListener("DOMContentLoaded", boot);
})();
