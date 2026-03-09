(() => {
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

      const connectBtn = document.createElement("button");
      connectBtn.className = "btn btn-outline-primary btn-sm";
      connectBtn.textContent = "Verbinden";
      connectBtn.addEventListener("click", async () => {
        try {
          const ssid = net.ssid || "";
          if (!ssid || ssid === "<hidden>") {
            toast("Hidden SSID bitte manuell unten eintragen.", "secondary");
            return;
          }
          const pw = window.prompt(`Passwort für \"${ssid}\" (leer für open/WPS):`, "") || "";
          await fetchJson("/api/wifi/connect", {
            method: "POST",
            body: { ssid, password: pw, ifname: "wlan0", hidden: false },
          });
          toast(`WLAN verbunden/angefragt: ${ssid}`, "success");
          await refreshAll();
        } catch (err) {
          toast(err.message || String(err), "danger");
        }
      });

      const wpsBtn = document.createElement("button");
      wpsBtn.className = "btn btn-outline-secondary btn-sm";
      wpsBtn.textContent = "WPS";
      wpsBtn.addEventListener("click", async () => {
        try {
          await fetchJson("/api/network/wifi/wps/start", {
            method: "POST",
            body: { ifname: "wlan0", ssid: net.ssid || "", bssid: net.bssid || "" },
          });
          toast("WPS gestartet.", "success");
          await refreshWpsAndLogs();
        } catch (err) {
          toast(err.message || String(err), "danger");
        }
      });

      actions.append(connectBtn, wpsBtn);
      row.append(top, meta, actions);
      host.append(row);
    }
  }

  async function refreshWifiStatus() {
    const payload = await fetchJson("/api/network/wifi/status");
    renderWifiStatus(payload);
  }

  async function refreshScan() {
    const payload = await fetchJson("/api/wifi/scan");
    renderScanList(payload);
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

  async function startWps() {
    await fetchJson("/api/network/wifi/wps/start", {
      method: "POST",
      body: { ifname: "wlan0" },
    });
    toast("WPS gestartet.", "success");
    await refreshWpsAndLogs();
  }

  async function refreshAll() {
    await refreshWifiStatus();
    await refreshScan();
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
    q("btn-wifi-setup-connect").addEventListener("click", () => run(manualConnect));
    q("btn-wifi-setup-wps").addEventListener("click", () => run(startWps));
    q("btn-wifi-setup-wps-refresh").addEventListener("click", () => run(refreshWpsAndLogs));
  }

  window.addEventListener("DOMContentLoaded", async () => {
    bind();
    await run(refreshAll);
  });
})();
