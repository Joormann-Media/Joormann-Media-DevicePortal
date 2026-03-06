(() => {
  const els = {};
  let networkState = null;
  let wifiProfilesState = null;
  let selectedWpsTarget = null;
  let wpsPollHandle = null;
  let apPollHandle = null;
  let apClientsInitialized = false;
  let apKnownConnectedMacs = new Set();

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
        throw new Error(`Request timeout after ${Math.round(timeoutMs / 1000)}s`);
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

  function clearNode(el) {
    while (el && el.firstChild) {
      el.removeChild(el.firstChild);
    }
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

  function renderStatus(data) {
    const state = data.state || {};
    const cfg = data.config || {};
    const panel = (state.panel || cfg.panel_link_state || {});
    const linked = !!panel.linked;
    const online = !!state.hostname;

    setStatusBadge(linked, online);

    q("status-hostname").textContent = state.hostname || "-";
    q("status-ip").textContent = state.ip || "-";
    q("status-linked").textContent = yn(linked);
    q("status-device-slug").textContent = state.device_slug || cfg.device_slug || "-";
    q("status-stream-slug").textContent = state.selected_stream_slug || cfg.selected_stream_slug || "-";
    q("status-last-check").textContent = panel.last_check || "-";
    q("status-last-error").textContent = panel.last_error || "-";

    if (!els.adminBase.value) {
      els.adminBase.value = cfg.admin_base_url || "";
    }
    if (!els.regToken.value) {
      els.regToken.value = cfg.registration_token || "";
    }
    if (!els.deviceSlug.value) {
      els.deviceSlug.value = cfg.device_slug || "";
    }
    if (!els.streamSlug.value) {
      els.streamSlug.value = cfg.selected_stream_slug || "";
    }
  }

  function renderNetwork(payload) {
    const data = payload.data || payload;
    networkState = data;

    const lan = (data.interfaces || {}).lan || {};
    const wifi = (data.interfaces || {}).wifi || {};
    const bt = (data.interfaces || {}).bluetooth || {};
    const routes = data.routes || {};
    const tailscale = data.tailscale || {};

    q("net-hostname").textContent = data.hostname || "-";
    q("net-gateway").textContent = routes.gateway || "-";
    q("net-dns").textContent = (routes.dns || []).join(", ") || "-";
    q("net-tailscale").textContent = tailscale.present ? (tailscale.ip || "present") : "not present";

    q("lan-ifname").textContent = lan.ifname || "eth0";
    q("lan-enabled").textContent = yn(!!lan.enabled);
    q("lan-carrier").textContent = yn(!!lan.carrier);
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

    els.btnWifiToggle.textContent = wifi.enabled ? "Disable Wi-Fi" : "Enable Wi-Fi";
    els.btnBtToggle.textContent = bt.enabled ? "Disable Bluetooth" : "Enable Bluetooth";
    els.btnLanToggle.textContent = lan.enabled ? "Disable LAN" : "Enable LAN";
  }

  async function refreshStatus() {
    const data = await fetchJson("/api/status");
    renderStatus(data);
    return data;
  }

  async function refreshState() {
    const data = await fetchJson("/api/status/state");
    const base = await fetchJson("/api/status");
    base.state = data.state || base.state;
    renderStatus(base);
    return base;
  }

  async function refreshNetwork() {
    const data = await fetchJson("/api/network/info");
    renderNetwork(data);
    return data;
  }

  function renderApStatus(payload) {
    const data = payload.data || payload || {};
    const active = !!data.active;
    q("ap-ssid").textContent = data.ssid || "-";
    q("ap-ip").textContent = data.ip || "-";
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

  function renderWifiScan(networks) {
    const host = q("wifi-scan-list");
    clearNode(host);
    if (!Array.isArray(networks) || networks.length === 0) {
      const empty = document.createElement("div");
      empty.className = "text-secondary";
      empty.textContent = "Keine WLAN Netze gefunden.";
      host.append(empty);
      return;
    }
    for (const item of networks) {
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
      meta.textContent = `Security: ${item.security || "OPEN"}`;

      const actions = document.createElement("div");
      actions.className = "d-flex gap-2";
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
      connectBtn.addEventListener("click", () => run(() => connectSsid(item.ssid || "")));
      const wpsBtn = document.createElement("button");
      wpsBtn.className = "btn btn-outline-secondary btn-sm";
      wpsBtn.textContent = "WPS";
      wpsBtn.addEventListener("click", () => run(() => startWps({ ssid: item.ssid || "", bssid: item.bssid || "" })));
      actions.append(selectBtn, connectBtn, wpsBtn);
      row.append(top, meta, actions);
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
      upBtn.addEventListener("click", () => run(() => wifiProfileUp(ssid)));
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
      actions.append(upBtn, wpsBtn, prefBtn, delBtn);
      row.append(top, actions);
      host.append(row);
    }
  }

  async function refreshWifiProfiles() {
    const payload = await fetchJson("/api/wifi/profiles");
    renderWifiProfiles(payload);
    return payload;
  }

  async function wifiProfileUp(ssid) {
    await fetchJson("/api/wifi/profiles/up", { method: "POST", body: { ssid } });
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
    const data = await fetchJson("/api/panel/link-status");
    q("status-linked").textContent = yn(!!data.linked);
    q("status-last-check").textContent = (data.panel_link_state || {}).last_check || "-";
    q("status-last-error").textContent = (data.panel_link_state || {}).last_error || "-";
    toast("Panel link status refreshed", "success");
  }

  async function panelUnlink() {
    await fetchJson("/api/panel/unlink", { method: "POST" });
    await refreshStatus();
    toast("Panel unlinked", "success");
  }

  async function panelPing() {
    await fetchJson("/api/panel/ping", {
      method: "POST",
      body: { admin_base_url: els.adminBase.value || "" },
    });
    await refreshStatus();
    toast("Panel ping completed", "success");
  }

  async function panelRegister() {
    await fetchJson("/api/panel/register", {
      method: "POST",
      body: {
        admin_base_url: els.adminBase.value || "",
        registration_token: els.regToken.value || "",
      },
    });
    await refreshStatus();
    toast("Panel register completed", "success");
  }

  async function panelTestUrl() {
    await fetchJson("/api/panel/test-url", {
      method: "POST",
      body: { url: els.adminBase.value || "" },
    });
    toast("Panel URL saved", "success");
  }

  async function pullPlan() {
    await fetchJson("/api/plan/pull", {
      method: "POST",
      body: {
        admin_base_url: els.adminBase.value || "",
        deviceSlug: els.deviceSlug.value || "",
        streamSlug: els.streamSlug.value || "",
      },
    });
    await refreshStatus();
    toast("Plan pulled", "success");
  }

  async function refreshFingerprint() {
    await fetchJson("/api/status/fingerprint/refresh", { method: "POST" });
    toast("Fingerprint refreshed", "success");
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
    toast("Bluetooth updated", "success");
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

  async function updatePortal() {
    const btn = q("btn-system-update-portal");
    const logEl = q("system-update-log");
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Update läuft...';
    try {
      const payload = await fetchJson("/api/system/portal/update", { method: "POST", timeoutMs: 220000 });
      const data = payload.data || {};
      const lines = [
        `message: ${data.message || "-"}`,
        `repo: ${data.repo_dir || "-"}`,
        `user: ${data.service_user || "-"}`,
        `service: ${data.service_name || "-"}`,
        `git_status: ${data.git_status || "-"}`,
        `restart_scheduled: ${String(!!data.restart_scheduled)}`,
        data.details ? `details: ${data.details}` : "",
      ].filter(Boolean);
      logEl.textContent = lines.join("\n");
      toast(data.message || "Update ausgelöst. Service wird neu gestartet.", "success");
    } finally {
      btn.disabled = false;
      btn.innerHTML = original;
    }
  }

  function bindButtons() {
    q("btn-refresh-status").addEventListener("click", () => run(refreshState));
    q("btn-refresh-fingerprint").addEventListener("click", () => run(refreshFingerprint));
    q("btn-pull-plan").addEventListener("click", () => run(pullPlan));
    q("btn-check-link").addEventListener("click", () => run(panelCheckLink));

    q("btn-panel-test").addEventListener("click", () => run(panelTestUrl));
    q("btn-panel-ping").addEventListener("click", () => run(panelPing));
    q("btn-panel-register").addEventListener("click", () => run(panelRegister));

    els.btnWifiToggle.addEventListener("click", () => run(toggleWifi));
    els.btnBtToggle.addEventListener("click", () => run(toggleBluetooth));
    els.btnLanToggle.addEventListener("click", () => run(toggleLan));
    q("btn-wps").addEventListener("click", () => run(startWps));
    q("btn-refresh-network").addEventListener("click", () => run(refreshNetwork));
    q("btn-wifi-scan").addEventListener("click", () => run(refreshWifiScan));
    q("btn-wifi-profiles-refresh").addEventListener("click", () => run(refreshWifiProfiles));
    q("btn-wifi-profiles-apply").addEventListener("click", () => run(wifiProfilesApply));
    q("btn-wifi-manual-add").addEventListener("click", () => run(wifiProfilesAddManual));
    q("btn-wifi-logs-refresh").addEventListener("click", () => run(refreshWifiLogs));
    q("btn-ap-enable").addEventListener("click", () => run(() => toggleAp(true)));
    q("btn-ap-disable").addEventListener("click", () => run(() => toggleAp(false)));
    q("btn-ap-refresh").addEventListener("click", () => run(async () => {
      await refreshApStatus();
      await refreshApClients();
    }));
    q("btn-system-update-portal").addEventListener("click", () => run(updatePortal));
    q("btn-fix-tailscale-dns").addEventListener("click", () => run(fixTailscaleDns));
    q("btn-system-refresh-network").addEventListener("click", () => run(refreshNetwork));

    q("btn-confirm-unlink").addEventListener("click", async () => {
      const modal = bootstrap.Modal.getOrCreateInstance(q("unlinkModal"));
      modal.hide();
      await run(panelUnlink);
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
    els.btnLanToggle = q("btn-lan-toggle");
  }

  async function boot() {
    initRefs();
    bindButtons();
    await run(refreshStatus);
    await run(refreshNetwork);
    await run(refreshWifiScan);
    await run(refreshWifiProfiles);
    await run(refreshWpsStatus);
    await run(refreshWifiLogs);
    await run(refreshApStatus);
    await run(refreshApClients);
    startApPolling();
    setWpsTarget(null);
  }

  window.addEventListener("DOMContentLoaded", boot);
})();
