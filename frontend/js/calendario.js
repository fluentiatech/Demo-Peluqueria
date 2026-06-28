"use strict";
(() => {
  const DAYS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"];
  let biz = null, business = null, closures = [];

  // ---- Carga ----
  async function load() {
    const c = document.getElementById("content");
    c.innerHTML = '<div class="skel" style="height:380px"></div>';
    try {
      [business, closures] = await Promise.all([
        Panel.api(`/admin/businesses/${biz}`),
        Panel.api(`/admin/businesses/${biz}/closures`),
      ]);
      shell();
      renderWeek();
      renderClosures();
      checkDay();
    } catch (e) {
      c.innerHTML = `<div class="err">${Panel.esc(e.message)}</div>`;
    }
  }

  function shell() {
    document.getElementById("content").innerHTML =
      '<div class="head"><h1>Calendario</h1>' +
      '<div class="sub">El horario definido aquí es el que usa el agente de WhatsApp para saber si puede dar cita ese día y a esa hora.</div></div>' +

      // --- Comprobador de un día ---
      '<div class="card"><div class="card-head"><h3>Comprobar un día</h3></div>' +
      '<div style="padding:16px 18px">' +
      '<div class="cal-check-row">' +
      `<input type="date" id="chk-date" value="${Panel.todayISO()}">` +
      '<input type="time" id="chk-time" title="Hora a comprobar (opcional)">' +
      '<button class="btn primary" id="chk-go">Comprobar</button></div>' +
      '<div id="chk-result" style="margin-top:14px"></div></div></div>' +

      // --- Horario semanal ---
      '<div class="card"><div class="card-head"><h3>Horario semanal</h3>' +
      '<span class="count" id="week-count"></span><div style="flex:1"></div>' +
      '<label style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em" ' +
      'title="Cada cuánto se ofrecen horas de inicio: 15 → 9:00, 9:15, 9:30…">Citas cada</label>' +
      `<input type="number" id="gran" min="5" max="240" step="5" value="${business.slot_granularity_min}" style="width:74px;min-height:34px;text-align:right" title="Paso entre las horas de cita que se ofrecen">` +
      '<span class="muted" style="font-size:12px">min</span>' +
      '<button class="btn primary sm" id="week-save">Guardar horario</button></div>' +
      '<div class="cal-hint">Marca cada día como abierto o cerrado. Un solo tramo = horario continuo; dos tramos = horario partido (mañana y tarde).</div>' +
      '<div id="week-body"></div></div>' +

      // --- Días especiales / cierres ---
      '<div class="card"><div class="card-head"><h3>Días especiales y cierres</h3>' +
      '<span class="count" id="clo-count"></span><div style="flex:1"></div>' +
      '<button class="btn primary sm" id="clo-add">+ Añadir</button></div>' +
      '<div id="clo-body"></div></div>';

    document.getElementById("chk-go").onclick = checkDay;
    document.getElementById("chk-date").onchange = checkDay;
    document.getElementById("week-save").onclick = saveWeek;
    document.getElementById("clo-add").onclick = openClosure;
    document.getElementById("clo-body").addEventListener("click", onCloClick);
  }

  // ---- Comprobador ----
  async function checkDay() {
    const box = document.getElementById("chk-result");
    if (!box) return;
    const d = document.getElementById("chk-date").value;
    const t = document.getElementById("chk-time").value;
    box.innerHTML = '<div class="skel" style="height:56px"></div>';
    try {
      const info = await Panel.api(`/admin/businesses/${biz}/day-info?date=${d}`);
      const open = info.is_open;
      const kindLbl = { cerrado: "Cerrado", continuo: "Horario continuo", partido: "Horario partido" }[info.kind];
      const hours = info.intervals.map((h) => `${h[0]}–${h[1]}`).join(" · ") || "—";
      let html = `<div class="day-badge"><span class="dot ${open ? "on" : "off"}"></span>${open ? "Abierta" : "Cerrada"} · ${kindLbl}</div>`;
      if (open) html += `<div class="num" style="margin-top:6px;font-size:16px">${hours}</div>`;
      if (info.is_special) html += `<div class="muted" style="margin-top:5px;font-size:12.5px">Día especial${info.reason ? " · " + Panel.esc(info.reason) : ""}</div>`;
      if (t) {
        const yes = open && info.intervals.some((h) => t >= h[0] && t < h[1]);
        html += `<div style="margin-top:11px"><span class="chip ${yes ? "completed" : "no_show"}">${yes ? "Abierta" : "Cerrada"} a las ${t}</span></div>`;
      }
      box.innerHTML = html;
    } catch (e) { box.innerHTML = `<div class="err">${Panel.esc(e.message)}</div>`; }
  }

  // ---- Horario semanal ----
  function calDay(d, label) {
    const tramos = (business.opening_hours && business.opening_hours[String(d)]) || [];
    const open = tramos.length > 0;
    const t1 = tramos[0] || ["", ""], t2 = tramos[1] || ["", ""];
    const dis = open ? "" : "disabled";
    return `<div class="cal-day" data-d="${d}"><div class="lbl">${label}</div>` +
      `<button class="btn sm tgl ${open ? "primary" : ""}" data-tgl="${d}">${open ? "Abierta" : "Cerrada"}</button>` +
      `<div class="tramo"><span>Tramo 1</span>` +
      `<input type="time" id="d${d}-1a" value="${t1[0]}" ${dis}>` +
      `<input type="time" id="d${d}-1b" value="${t1[1]}" ${dis}></div>` +
      `<div class="tramo"><span>Tramo 2</span>` +
      `<input type="time" id="d${d}-2a" value="${t2[0]}" ${dis}>` +
      `<input type="time" id="d${d}-2b" value="${t2[1]}" ${dis}></div></div>`;
  }

  function renderWeek() {
    document.getElementById("week-body").innerHTML = DAYS.map((l, d) => calDay(d, l)).join("");
    const openDays = DAYS.filter((_, d) => ((business.opening_hours || {})[String(d)] || []).length).length;
    document.getElementById("week-count").textContent = `· abierta ${openDays} día(s)/semana`;
    document.querySelectorAll("[data-tgl]").forEach((b) => {
      b.onclick = () => {
        const on = b.classList.toggle("primary");
        b.textContent = on ? "Abierta" : "Cerrada";
        const d = b.dataset.tgl;
        ["1a", "1b", "2a", "2b"].forEach((k) => { document.getElementById(`d${d}-${k}`).disabled = !on; });
      };
    });
  }

  async function saveWeek() {
    const oh = {}; let bad = null;
    DAYS.forEach((_, d) => {
      const open = document.querySelector(`[data-tgl="${d}"]`).classList.contains("primary");
      if (!open) return;
      const g = (k) => document.getElementById(`d${d}-${k}`).value;
      const tramos = [];
      if (g("1a") && g("1b")) tramos.push([g("1a"), g("1b")]);
      if (g("2a") && g("2b")) tramos.push([g("2a"), g("2b")]);
      if (!tramos.length) bad = DAYS[d]; else oh[String(d)] = tramos;
    });
    if (bad) { alert(`"${bad}" está marcada como abierta pero sin horas. Rellena el Tramo 1 o márcala como cerrada.`); return; }
    const gran = Number(document.getElementById("gran").value) || business.slot_granularity_min;
    const prev = business.opening_hours;
    try {
      business = await Panel.api(`/admin/businesses/${biz}`,
        { method: "PATCH", body: JSON.stringify({ opening_hours: oh, slot_granularity_min: gran }) });
      renderWeek(); checkDay();
      Panel.undo("Horario semanal guardado", async () => {
        business = await Panel.api(`/admin/businesses/${biz}`,
          { method: "PATCH", body: JSON.stringify({ opening_hours: prev }) });
        renderWeek(); checkDay();
      });
    } catch (e) { alert(e.message); }
  }

  // ---- Días especiales / cierres ----
  function renderClosures() {
    const body = document.getElementById("clo-body");
    document.getElementById("clo-count").textContent = closures.length ? `· ${closures.length}` : "";
    if (!closures.length) {
      body.innerHTML = '<div class="empty">Sin días especiales. Añade festivos, cierres o aperturas puntuales.</div>';
      return;
    }
    const sorted = [...closures].sort((a, b) => a.date.localeCompare(b.date));
    body.innerHTML = sorted.map((c) => {
      const when = new Date(c.date + "T00:00:00")
        .toLocaleDateString("es-ES", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
      const what = c.is_closed
        ? '<span class="chip no_show">Cerrada todo el día</span>'
        : `<span class="chip confirmed">Especial · ${c.custom_hours.map((h) => h[0] + "–" + h[1]).join(" · ")}</span>`;
      return `<div class="appt"><div class="time" style="width:auto;min-width:128px;font-weight:600">${when}</div>` +
        `<div class="who"><div class="name">${what}</div>` +
        (c.reason ? `<div class="meta">${Panel.esc(c.reason)}</div>` : "") + "</div>" +
        `<button class="btn ghost sm danger" data-delclo="${c.id}">Eliminar</button></div>`;
    }).join("");
  }

  function onCloClick(e) {
    const del = e.target.closest("[data-delclo]");
    if (del) removeClosure(del.dataset.delclo);
  }

  async function removeClosure(id) {
    const c = closures.find((x) => x.id === id);
    if (!confirm("¿Eliminar este día especial?")) return;
    try {
      await Panel.api(`/admin/businesses/${biz}/closures/${id}`, { method: "DELETE" });
      closures = closures.filter((x) => x.id !== id);
      renderClosures(); checkDay();
      Panel.undo("Día especial eliminado", async () => {
        const re = await Panel.api(`/admin/businesses/${biz}/closures`, {
          method: "POST",
          body: JSON.stringify({
            date: c.date, is_closed: c.is_closed, custom_hours: c.custom_hours, reason: c.reason,
          }),
        });
        closures.push(re); renderClosures(); checkDay();
      });
    } catch (e) { alert(e.message); }
  }

  function openClosure() {
    const f2 = (l, inner) => `<div class="field2"><label>${l}</label>${inner}</div>`;
    const mod = Panel.modal({
      title: "Día especial o cierre",
      submitLabel: "Guardar",
      html:
        f2("Fecha", `<input type="date" id="c-date" value="${Panel.todayISO()}">`) +
        f2("Tipo", '<select id="c-mode"><option value="closed">Cerrada todo el día</option>' +
          '<option value="special">Apertura especial</option></select>') +
        '<div id="c-hours" style="display:none;gap:12px;grid-template-columns:1fr 1fr" class="two">' +
        f2("Mañana de", '<input type="time" id="c-1a">') + f2("a", '<input type="time" id="c-1b">') +
        f2("Tarde de", '<input type="time" id="c-2a">') + f2("a", '<input type="time" id="c-2b">') + "</div>" +
        f2("Motivo", '<input id="c-reason" placeholder="Festivo, evento, vacaciones…">') +
        '<div id="c-err" class="err" style="display:none"></div>',
      onSubmit: async (m, close) => {
        const v = (id) => m.querySelector(id).value.trim();
        const err = m.querySelector("#c-err");
        const mode = m.querySelector("#c-mode").value;
        const body = { date: v("#c-date"), is_closed: mode === "closed", reason: v("#c-reason") || null, custom_hours: [] };
        if (!body.date) { err.style.display = "block"; err.textContent = "Indica la fecha."; return; }
        if (mode === "special") {
          const h = [];
          if (v("#c-1a") && v("#c-1b")) h.push([v("#c-1a"), v("#c-1b")]);
          if (v("#c-2a") && v("#c-2b")) h.push([v("#c-2a"), v("#c-2b")]);
          if (!h.length) { err.style.display = "block"; err.textContent = "Indica al menos un tramo horario."; return; }
          body.custom_hours = h;
        }
        try {
          const created = await Panel.api(`/admin/businesses/${biz}/closures`,
            { method: "POST", body: JSON.stringify(body) });
          closures.push(created); renderClosures(); checkDay(); close();
          Panel.flash("Día especial guardado");
        } catch (e) { err.style.display = "block"; err.textContent = e.message; }
      },
    });
    const hours = mod.el.querySelector("#c-hours");
    mod.el.querySelector("#c-mode").onchange = (e) => {
      hours.style.display = e.target.value === "special" ? "grid" : "none";
    };
  }

  Panel.onBusiness((b) => { biz = b; load(); });
  Panel.mount("calendario");
})();
