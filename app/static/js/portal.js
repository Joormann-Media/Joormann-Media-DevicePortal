(() => {
  const els = {};
  let networkState = null;

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

  async function startWps() {
    const btn = q("btn-wps");
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>WPS startet...';
    toast("WPS wird gestartet...", "secondary");
    try {
      const payload = await fetchJson("/api/network/wps", { method: "POST", timeoutMs: 20000 });
      const message = payload.message || "WPS wurde gestartet. Bitte jetzt innerhalb von 2 Minuten am Router die WPS-Taste druecken.";
      const hint = payload.hint || "Je nach Router kann die Verbindung 30-120 Sekunden dauern.";
      const net = ((payload.data || {}).network || {});
      const connectedInfo = net.ssid ? ` Verbunden mit SSID: ${net.ssid}.` : "";
      toast(`${message}${connectedInfo} ${hint}`, "success");
      await refreshNetwork();
      for (let i = 0; i < 10; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 6000));
        await refreshNetwork();
        const wifi = ((((networkState || {}).interfaces || {}).wifi) || {});
        if (wifi.connected) {
          toast(`WLAN verbunden: ${wifi.ssid || "SSID unbekannt"}`, "success");
          break;
        }
      }
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
  }

  window.addEventListener("DOMContentLoaded", boot);
})();
