(() => {
  let wifiRadioEnabled = null;
  let selectedWpsTarget = null;

  function q(id) {
    return document.getElementById(id);
  }

  function toast(message, type = "info") {
    const host = q("wifi-setup-alerts");
    if (!host) return;
    const cls = type === "danger" ? "danger" : type === "success" ? "success" : "secondary";
    const div = document.createElement("div");
    div.className = `alert alert-${cls} alert-dismissible fade show py-2 mb-2`;
    div.role = "alert";
    div.textContent = String(message || "");
    const close = document.createElement("button");
    close.type = "button";
    close.className = "btn-close";
    close.setAttribute("data-bs-dismiss", "alert");
    close.setAttribute("aria-label", "Close");
    div.append(close);
    host.prepend(div);
    setTimeout(() => {
      if (div.parentNode) div.remove();
    }, 5000);
  }

  async function fetchJson(url, options = {}) {
    const opts = {
      headers: { Accept: "application/json" },
      ...options,
    };
    if (opts.body && typeof opts.body !== "string") {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    const res = await fetch(url, opts);
    const txt = await res.text();
    let payload = {};
    try {
      payload = txt ? JSON.parse(txt) : {};
    } catch (_) {
      throw new Error(`Ungültige Serverantwort (HTTP ${res.status})`);
    }
    if (!res.ok || payload.ok === false) {
      const msg = (payload.error && payload.error.message) || payload.message || `HTTP ${res.status}`;
      throw new Error(msg);
    }
    return payload;
  }

  function yn(v) {
    return v ? "yes" : "no";
  }

  function renderWifiStatus(payload) {
    const data = payload.data || payload;
    q("wifi-setup-ifname").textContent = data.ifname || "wlan0";
    q("wifi-setup-connected").textContent = yn(!!data.connected);
    q("wifi-setup-ssid").textContent = data.ssid || "-";
    q("wifi-setup-ip").textContent = data.ip || "-";
    q("wifi-setup-signal").textContent = Number.isInteger(data.signal) ? `${data.signal}%` : (data.signal || "-");
    q("wifi-setup-wpa").textContent = data.wpa_state || "-";
  }

  function renderWpsStatus(payload) {
    const data = payload.data || {};
    const wps = data.wps || {};
    q("wifi-setup-wps-phase").textContent = `${wps.phase || "idle"} - ${wps.phase_message || ""}`.trim();
  }

  function renderWifiLogs(payload) {
    const data = payload.data || {};
    const events = Array.isArray(data.events) ? data.events : [];
    const logEl = q("wifi-setup-log");
    if (!events.length) {
      logEl.textContent = "-";
      return;
    }
    logEl.textContent = events
      .map((e) => `[${e.ts || ""}] ${(e.level || "info").toUpperCase()} ${e.message || ""}`)
      .join("\n");
    logEl.scrollTop = logEl.scrollHeight;
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

  function renderScanList(payload) {
    const data = payload.data || {};
    const networks = collapseMeshScanNetworks(Array.isArray(data.networks) ? data.networks : []);
    const host = q("wifi-setup-scan-list");
    host.innerHTML = "";
    if (!networks.length) {
      const empty = document.createElement("div");
      empty.className = "text-secondary";
      empty.textContent = "Keine WLAN Netze gefunden.";
      host.append(empty);
      return;
    }

    for (const net of networks) {
      const row = document.createElement("div");
      row.className = "list-group-item px-0";

      const top = document.createElement("div");
      top.className = "d-flex justify-content-between align-items-center gap-2";
      const title = document.createElement("strong");
      title.textContent = net.ssid || "<hidden>";
      const badge = document.createElement("span");
      badge.className = `badge ${net.in_use ? "text-bg-success" : "text-bg-secondary"}`;
      badge.textContent = net.in_use ? "connected" : `${net.signal || 0}%`;
      top.append(title, badge);

      const meta = document.createElement("div");
      meta.className = "text-secondary mb-2";
      const meshInfo = Number(net.mesh_nodes || 1) > 1 ? ` | Mesh-Knoten: ${Number(net.mesh_nodes || 1)}` : "";
      meta.textContent = `Security: ${net.security || "OPEN"}${meshInfo}`;

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

      const connectBtn = document.createElement("button");
      connectBtn.className = "btn btn-outline-primary btn-sm";
      connectBtn.textContent = "Verbinden";
      connectBtn.addEventListener("click", async () => {
        const ssid = net.ssid || "";
        if (!ssid || ssid === "<hidden>") {
          toast("Hidden SSID bitte manuell unten eintragen.", "secondary");
          return;
        }
        inlineConnect.classList.remove("d-none");
        inlinePw.focus();
      });

      inlineSubmit.addEventListener("click", async () => {
        try {
          const ssid = net.ssid || "";
          const pw = inlinePw.value || "";
          await fetchJson("/api/wifi/connect", {
            method: "POST",
            body: { ssid, password: pw, ifname: "wlan0", hidden: false },
          });
          toast(`WLAN verbunden/angefragt: ${ssid}`, "success");
          inlineConnect.classList.add("d-none");
          inlinePw.value = "";
          await refreshAll();
        } catch (err) {
          toast(err.message || String(err), "danger");
        }
      });
      inlineCancel.addEventListener("click", () => {
        inlineConnect.classList.add("d-none");
        inlinePw.value = "";
      });

      const wpsBtn = document.createElement("button");
      wpsBtn.className = "btn btn-outline-secondary btn-sm";
      wpsBtn.textContent = "WPS";
      wpsBtn.addEventListener("click", () => run(() => startWps({ ssid: net.ssid || "", bssid: "" }, "btn-wifi-setup-wps")));

      actions.append(connectBtn, wpsBtn);
      row.append(top, meta, actions, inlineConnect);
      host.append(row);
    }
  }

  function renderKnownProfiles(payload) {
    const data = payload.data || {};
    const unmanagedFallback = (Array.isArray(data.unmanaged) ? data.unmanaged : [])
      .map((item) => ({
        ssid: item.name || "",
        priority: Number.isFinite(item.priority) ? item.priority : 0,
        autoconnect: !!item.autoconnect,
        nm: { uuid: item.uuid || "" },
      }))
      .filter((item) => item.ssid);
    const profiles = (Array.isArray(data.profiles) && data.profiles.length)
      ? data.profiles
      : ((Array.isArray(data.configured) && data.configured.length) ? data.configured : unmanagedFallback);
    const host = q("wifi-setup-known-list");
    host.innerHTML = "";

    if (!profiles.length) {
      const empty = document.createElement("div");
      empty.className = "text-secondary";
      empty.textContent = "Keine bekannten WLAN-Profile.";
      host.append(empty);
      return;
    }

    for (const item of profiles) {
      const ssid = String(item.ssid || "").trim();
      if (!ssid) continue;

      const row = document.createElement("div");
      row.className = "list-group-item px-0";

      const top = document.createElement("div");
      top.className = "d-flex justify-content-between align-items-center gap-2";
      const title = document.createElement("strong");
      title.textContent = ssid;
      const meta = document.createElement("span");
      meta.className = "text-secondary";
      meta.textContent = `prio=${item.priority ?? 0} auto=${item.autoconnect ? "yes" : "no"}`;
      top.append(title, meta);

      const actions = document.createElement("div");
      actions.className = "d-flex gap-2 mt-2";

      const connectBtn = document.createElement("button");
      connectBtn.className = "btn btn-outline-primary btn-sm";
      connectBtn.textContent = "Verbinden";
      connectBtn.addEventListener("click", async () => {
        try {
          await fetchJson("/api/wifi/profiles/up", {
            method: "POST",
            body: { ssid, uuid: (item.nm && item.nm.uuid) ? String(item.nm.uuid) : "" },
          });
          toast(`WLAN-Profil aktiviert: ${ssid}`, "success");
          await refreshAll();
        } catch (err) {
          toast(err.message || String(err), "danger");
        }
      });

      const deleteBtn = document.createElement("button");
      deleteBtn.className = "btn btn-outline-danger btn-sm";
      deleteBtn.textContent = "Löschen";
      deleteBtn.addEventListener("click", async () => {
        const ok = window.confirm(`WLAN-Profil \"${ssid}\" auf dem Raspberry und im Portal wirklich löschen?`);
        if (!ok) return;
        try {
          await fetchJson("/api/wifi/profiles/delete", {
            method: "POST",
            body: { ssid, uuid: (item.nm && item.nm.uuid) ? String(item.nm.uuid) : "" },
          });
          toast(`WLAN-Profil gelöscht: ${ssid}`, "success");
          await refreshAll();
        } catch (err) {
          toast(err.message || String(err), "danger");
        }
      });

      actions.append(connectBtn, deleteBtn);
      row.append(top, actions);
      host.append(row);
    }
  }

  function renderRadioState(enabled) {
    wifiRadioEnabled = !!enabled;
    const el = q("wifi-setup-radio-status");
    if (el) {
      el.textContent = wifiRadioEnabled ? "AN" : "AUS";
    }
  }

  async function refreshWifiStatus() {
    const payload = await fetchJson("/api/network/wifi/status");
    renderWifiStatus(payload);
    return payload;
  }

  async function refreshScan() {
    const payload = await fetchJson("/api/wifi/scan");
    renderScanList(payload);
  }

  async function refreshKnownProfiles() {
    const payload = await fetchJson("/api/wifi/profiles");
    renderKnownProfiles(payload);
  }

  async function refreshRadioState() {
    const payload = await fetchJson("/api/network/info");
    const data = payload.data || {};
    const interfaces = data.interfaces || {};
    const wifi = interfaces.wifi || {};
    renderRadioState(!!wifi.enabled);
  }

  async function toggleWifiRadio(enabled) {
    await fetchJson("/api/network/wifi/toggle", {
      method: "POST",
      body: { enabled: !!enabled },
    });
    toast(enabled ? "WLAN Adapter eingeschaltet." : "WLAN Adapter ausgeschaltet.", "success");
    await refreshAll();
  }

  async function requestReboot() {
    const ok = window.confirm("Raspberry Pi jetzt neu starten?");
    if (!ok) return;
    await fetchJson("/api/system/power", {
      method: "POST",
      body: { action: "reboot" },
    });
    toast("Neustart wurde angefordert.", "success");
  }

  async function refreshWpsAndLogs() {
    const wps = await fetchJson("/api/network/wifi/wps/status");
    renderWpsStatus(wps);
    const logs = await fetchJson("/api/network/wifi/logs?limit=120");
    renderWifiLogs(logs);
  }

  async function manualConnect() {
    const ssid = (q("wifi-setup-manual-ssid").value || "").trim();
    const password = q("wifi-setup-manual-password").value || "";
    if (!ssid) {
      toast("SSID fehlt.", "danger");
      return;
    }
    await fetchJson("/api/wifi/connect", {
      method: "POST",
      body: { ssid, password, ifname: "wlan0", hidden: false },
    });
    toast(`WLAN verbunden/gespeichert: ${ssid}`, "success");
    q("wifi-setup-manual-password").value = "";
    await refreshAll();
  }

  async function startWps(target = null, triggerId = "btn-wifi-setup-wps-hero") {
    const btn = q(triggerId) || q("btn-wifi-setup-wps-hero") || q("btn-wifi-setup-wps");
    const original = btn ? btn.innerHTML : "";

    if (target && target.ssid) {
      selectedWpsTarget = {
        ssid: String(target.ssid || "").trim(),
        bssid: String(target.bssid || "").trim(),
      };
    }

    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>WPS startet...';
    }
    toast("WPS wird gestartet...", "secondary");

    try {
      let triggerError = null;
      const payloadBody = { ifname: "wlan0" };
      if (selectedWpsTarget && selectedWpsTarget.ssid) {
        payloadBody.target_ssid = selectedWpsTarget.ssid;
      }
      if (selectedWpsTarget && selectedWpsTarget.bssid) {
        payloadBody.target_bssid = selectedWpsTarget.bssid;
      }
      try {
        const payload = await fetchJson("/api/network/wifi/wps/start", {
          method: "POST",
          body: payloadBody,
          timeoutMs: 20000,
        });
        const message = payload.message || "WPS wurde gestartet. Bitte jetzt innerhalb von 2 Minuten am Router die WPS-Taste druecken.";
        const hint = payload.hint || "Je nach Router kann die Verbindung 30-120 Sekunden dauern.";
        toast(`${message} ${hint}`.trim(), "success");
      } catch (err) {
        triggerError = err instanceof Error ? err : new Error(String(err));
        const msg = triggerError.message || "Unbekannter Fehler";
        const probablyStarted = /wps(_| )?pbc|WPS wurde gestartet/i.test(msg);
        toast(
          probablyStarted
            ? "WPS wurde ausgelöst. Verbindung wird weiter geprüft (30-120 Sekunden möglich)."
            : `WPS-Trigger meldet Fehler, Verbindung wird trotzdem weiter geprüft: ${msg}`,
          "secondary",
        );
      }

      await refreshWifiStatus();
      await refreshWpsAndLogs();
      let connected = false;
      for (let i = 0; i < 10; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 6000));
        const payload = await refreshWifiStatus();
        await refreshWpsAndLogs();
        const wifi = (payload.data || {});
        if (wifi.connected) {
          toast(`WLAN verbunden: ${wifi.ssid || "SSID unbekannt"}`, "success");
          connected = true;
          break;
        }
      }

      if (!connected && triggerError) {
        toast("WPS wurde offenbar nicht sauber gestartet oder Verbindung blieb aus. Bitte WPS am Router erneut drücken und nochmal versuchen.", "danger");
      } else if (!connected) {
        toast("WPS gestartet, aber noch keine WLAN-Verbindung erkannt.", "secondary");
      }
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = original || "WPS starten";
      }
    }
  }

  async function refreshAll() {
    await refreshRadioState();
    await refreshWifiStatus();
    await refreshScan();
    await refreshKnownProfiles();
    await refreshWpsAndLogs();
  }

  async function run(fn) {
    try {
      await fn();
    } catch (err) {
      toast(err.message || String(err), "danger");
    }
  }

  function bind() {
    q("btn-wifi-setup-refresh").addEventListener("click", () => run(refreshAll));
    q("btn-wifi-setup-scan").addEventListener("click", () => run(refreshScan));
    q("btn-wifi-setup-known-refresh").addEventListener("click", () => run(refreshKnownProfiles));
    q("btn-wifi-setup-connect").addEventListener("click", () => run(manualConnect));
    q("btn-wifi-setup-wps").addEventListener("click", () => run(() => startWps(null, "btn-wifi-setup-wps")));
    q("btn-wifi-setup-wps-hero").addEventListener("click", () => run(() => startWps(null, "btn-wifi-setup-wps-hero")));
    q("btn-wifi-setup-wps-refresh").addEventListener("click", () => run(refreshWpsAndLogs));
    q("btn-wifi-setup-radio-on").addEventListener("click", () => run(() => toggleWifiRadio(true)));
    q("btn-wifi-setup-radio-off").addEventListener("click", () => run(() => toggleWifiRadio(false)));
    q("btn-wifi-setup-reboot").addEventListener("click", () => run(requestReboot));
  }

  window.addEventListener("DOMContentLoaded", async () => {
    bind();
    await run(refreshAll);
  });
})();
