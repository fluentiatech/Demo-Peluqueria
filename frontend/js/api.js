/* Cliente compartido del panel: auth por cookie de sesión (+CSRF), negocio activo,
   helpers y undo. La API key se cambia por una cookie HttpOnly vía /admin/session;
   nunca se guarda en el navegador (un XSS no puede robarla). */
"use strict";
const Panel = (() => {
  const K_BIZ = "panel_biz";
  let businesses = [], onBizCb = null, current = null, activePage = "", csrf = "";

  const content = () => document.getElementById("content");

  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // Primer carácter en mayúscula (deja el resto tal cual): datos consistentes.
  const capFirst = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
  // Capitaliza la primera letra en vivo conservando la posición del cursor.
  const autoCapFirst = (input) => {
    input.addEventListener("input", () => {
      const pos = input.selectionStart;
      const capped = capFirst(input.value);
      if (capped !== input.value) { input.value = capped; input.setSelectionRange(pos, pos); }
    });
  };

  const cur = () => {
    const b = businesses.find((x) => x.id === current);
    return (b && b.currency) || "EUR";
  };
  const money = (v) =>
    new Intl.NumberFormat("es-ES", { style: "currency", currency: cur() }).format(Number(v || 0));
  const num = (v) => new Intl.NumberFormat("es-ES").format(Number(v || 0));
  // Siempre en hora de España, sea cual sea la zona del navegador.
  const TZ = "Europe/Madrid";
  const time = (iso) =>
    new Date(iso).toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit", timeZone: TZ });
  const date = (iso) =>
    iso ? new Date(iso).toLocaleDateString("es-ES", { day: "2-digit", month: "2-digit", year: "2-digit", timeZone: TZ }) : "—";
  const todayISO = () => {
    const d = new Date();
    return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  };

  async function api(path, opts = {}) {
    const method = (opts.method || "GET").toUpperCase();
    const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
    if (csrf && method !== "GET") headers["X-CSRF-Token"] = csrf; // protección CSRF
    const r = await fetch(path, { ...opts, headers, credentials: "same-origin" });
    if (r.status === 401) {
      gate("Inicia sesión para continuar.");
      throw new Error("401");
    }
    if (!r.ok) {
      let d = null;
      try { d = await r.json(); } catch (e) { /* noop */ }
      throw new Error((d && d.detail) || "Error " + r.status);
    }
    return r.status === 204 ? null : r.json();
  }

  const NAV = [
    ["", "Inicio"], ["agenda", "Agenda"], ["espera", "Espera"], ["calendario", "Calendario"],
    ["clientes", "Clientes"], ["servicios", "Servicios"], ["facturacion", "Facturación"],
    ["ajustes", "Ajustes"],
  ];

  // Aplica la marca del negocio (color de acento, logo y nombre) al panel.
  function applyBranding() {
    const b = businesses.find((x) => x.id === current);
    if (!b) return;
    const color = /^#[0-9a-fA-F]{6}$/.test(b.brand_color || "") ? b.brand_color : "#0a0a0b";
    document.documentElement.style.setProperty("--brand", color);
    const brand = document.querySelector(".brand");
    if (brand) {
      const logo = b.logo_url
        ? `<img src="${esc(b.logo_url)}" alt="" class="brand-logo">` : "";
      brand.innerHTML = `${logo}<span>${esc(b.name)}<small>Panel de gestión</small></span>`;
    }
  }

  // Reemplaza un negocio en memoria y re-pinta selector + marca (tras editar Ajustes).
  function setBusiness(updated) {
    const i = businesses.findIndex((x) => x.id === updated.id);
    if (i >= 0) businesses[i] = updated; else businesses.push(updated);
    fillBiz(); applyBranding();
  }

  function registerSW() {
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/panel/sw.js").catch(() => {});
    }
  }

  function renderTopbar() {
    const row = document.getElementById("topbar-row");
    const nav = document.getElementById("topbar-nav");
    if (row) {
      row.innerHTML =
        '<div class="brand">Recepción<small>Panel de gestión</small></div>' +
        '<div class="spacer"></div>' +
        '<div class="field"><label>Negocio</label><select id="biz-select"></select></div>' +
        '<div class="field"><label>&nbsp;</label><button class="btn sm" id="key-btn">Salir</button></div>';
      const kb = document.getElementById("key-btn");
      if (kb) kb.onclick = logout;
    }
    if (nav) {
      nav.innerHTML = NAV.map(
        ([p, l]) => `<a href="/panel/${p ? p + ".html" : ""}" class="${p === activePage ? "active" : ""}">${l}</a>`
      ).join("");
    }
  }

  function fillBiz() {
    const sel = document.getElementById("biz-select");
    if (!sel) return;
    sel.innerHTML = businesses
      .map((b) => `<option value="${esc(b.id)}">${esc(b.name)}</option>`).join("");
    sel.value = current;
    sel.onchange = () => {
      current = sel.value;
      sessionStorage.setItem(K_BIZ, current);
      applyBranding();
      if (onBizCb) onBizCb(current);
    };
    applyBranding();
  }

  function gate(msg) {
    const c = content();
    if (!c) return;
    c.innerHTML =
      '<div class="gate"><h2>Acceso al panel</h2>' +
      (msg ? `<p class="err">${esc(msg)}</p>` : '<p class="muted">Introduce la clave del back-office.</p>') +
      '<input id="gate-key" type="password" placeholder="Clave del panel" autocomplete="off">' +
      '<input id="gate-totp" type="text" inputmode="numeric" placeholder="Código 2FA (si aplica)" autocomplete="one-time-code">' +
      '<button class="btn primary" id="gate-go" style="width:100%">Entrar</button></div>';
    const input = document.getElementById("gate-key");
    const totp = document.getElementById("gate-totp");
    const go = async () => {
      try {
        const r = await fetch("/admin/session", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ api_key: input.value.trim(), totp: totp.value.trim() || null }),
        });
        if (r.status === 429) return gate("Demasiados intentos. Espera unos minutos.");
        if (!r.ok) return gate("Credenciales inválidas.");
        const d = await r.json();
        csrf = d.csrf; // token CSRF en memoria (no en almacenamiento persistente)
        mount(activePage);
      } catch (e) { gate(e.message); }
    };
    document.getElementById("gate-go").onclick = go;
    totp.onkeydown = (e) => { if (e.key === "Enter") go(); };
    input.onkeydown = (e) => { if (e.key === "Enter") go(); };
    input.focus();
  }

  async function logout() {
    try { await fetch("/admin/session", { method: "DELETE", credentials: "same-origin" }); }
    catch (e) { /* noop */ }
    csrf = "";
    gate("Sesión cerrada.");
  }

  async function mount(active) {
    activePage = active || "";
    registerSW();
    renderTopbar();
    // No exigimos clave de antemano: lo intentamos. En desarrollo el back-office
    // está abierto y funciona sin clave; si el servidor responde 401, api() ya
    // muestra la pantalla de clave automáticamente.
    try { businesses = await api("/admin/businesses"); }
    catch (e) { if (e.message !== "401") gate(e.message); return; }
    if (!businesses.length) {
      content().innerHTML = '<div class="empty">No hay negocios dados de alta todavía.</div>';
      return;
    }
    current = sessionStorage.getItem(K_BIZ);
    if (!businesses.find((b) => b.id === current)) current = businesses[0].id;
    sessionStorage.setItem(K_BIZ, current);
    fillBiz();
    if (onBizCb) onBizCb(current);
  }

  const onBusiness = (cb) => { onBizCb = cb; };

  // --- Undo / toast ---
  let toastTimer = null;
  function undo(label, revertFn) {
    let el = document.getElementById("toast");
    if (!el) { el = document.createElement("div"); el.id = "toast"; document.body.appendChild(el); }
    el.innerHTML = `<span>${esc(label)}</span><button id="undo-btn">Deshacer</button>`;
    el.classList.add("show");
    clearTimeout(toastTimer);
    const hide = () => el.classList.remove("show");
    document.getElementById("undo-btn").onclick = async () => { hide(); await revertFn(); };
    toastTimer = setTimeout(hide, 6000);
  }
  function flash(label) { undo(label, () => {}); const b = document.getElementById("undo-btn"); if (b) b.style.display = "none"; }

  const STATUS = {
    pending: "Pendiente", confirmed: "Confirmada", completed: "Asistió", no_show: "No vino",
  };
  // Estados que el operador marca en la agenda (la cita nace "Pendiente").
  const STATUS_ORDER = ["pending", "completed", "no_show"];
  const chip = (s) => `<span class="chip ${s}">${STATUS[s] || s}</span>`;

  // Modal/popup reutilizable. onSubmit(modalEl, close) lee los campos y cierra.
  function modal({ title, html, submitLabel = "Guardar", onSubmit }) {
    const bg = document.createElement("div");
    bg.className = "modal-bg";
    bg.innerHTML =
      `<div class="modal"><h2>${esc(title)}</h2><div class="body">${html}</div>` +
      '<div class="foot"><button class="btn ghost" data-cancel>Cancelar</button>' +
      `<button class="btn primary" data-ok>${esc(submitLabel)}</button></div></div>`;
    document.body.appendChild(bg);
    const close = () => bg.remove();
    bg.querySelector("[data-cancel]").onclick = close;
    bg.onclick = (e) => { if (e.target === bg) close(); };
    const ok = bg.querySelector("[data-ok]");
    ok.onclick = async () => {
      ok.disabled = true;
      try { await onSubmit(bg, close); } catch (err) { alert(err.message); }
      finally { ok.disabled = false; }
    };
    return { el: bg, close };
  }

  return { api, mount, onBusiness, biz: () => current, setBusiness, esc, capFirst,
           autoCapFirst, money, num, time, date, todayISO, undo, flash, chip, modal,
           STATUS, STATUS_ORDER };
})();
