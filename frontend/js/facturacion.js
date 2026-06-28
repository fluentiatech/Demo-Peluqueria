"use strict";
(() => {
  let biz = null;
  const today = Panel.todayISO();
  let from = today.slice(0, 8) + "01", to = today;

  function shell() {
    document.getElementById("content").innerHTML =
      '<div class="head"><h1>Facturación</h1><div class="sub" id="f-sub"></div>' +
      '<div class="spacer"></div>' +
      `<input type="date" id="from" value="${from}"><span class="muted">→</span>` +
      `<input type="date" id="to" value="${to}">` +
      '<button class="btn sm" id="month">Este mes</button>' +
      '<button class="btn sm" id="d30">30 días</button></div>' +
      '<div id="f-body"></div>';
    const on = (id, fn) => (document.getElementById(id).onchange = fn);
    on("from", (e) => { from = e.target.value; load(); });
    on("to", (e) => { to = e.target.value; load(); });
    document.getElementById("month").onclick = () => { from = today.slice(0, 8) + "01"; to = today; sync(); load(); };
    document.getElementById("d30").onclick = () => {
      const d = new Date(); d.setDate(d.getDate() - 29);
      from = d.toISOString().slice(0, 10); to = today; sync(); load();
    };
  }
  function sync() { document.getElementById("from").value = from; document.getElementById("to").value = to; }

  async function load() {
    const body = document.getElementById("f-body");
    body.innerHTML = '<div class="skel" style="height:240px"></div>';
    try {
      const b = await Panel.api(`/admin/businesses/${biz}/billing?date_from=${from}&date_to=${to}`);
      render(b);
    } catch (e) {
      body.innerHTML = `<div class="err">${Panel.esc(e.message)}</div>`;
    }
  }

  function render(b) {
    document.getElementById("f-sub").textContent = `${Panel.date(b.date_from)} – ${Panel.date(b.date_to)}`;
    const statusName = Panel.STATUS;
    const counts = b.by_status.map((s) => `${statusName[s.status] || s.status}: ${Panel.num(s.count)}`).join("  ·  ");
    document.getElementById("f-body").innerHTML =
      '<div class="stats">' +
      st("Facturado (asistió)", Panel.money(b.revenue_billed)) +
      st("Previsto (por venir)", Panel.money(b.revenue_expected)) +
      st("Perdido (no-show)", Panel.money(b.revenue_lost)) +
      st("Citas en el periodo", Panel.num(b.appointments)) +
      "</div>" +
      `<div class="muted" style="margin-top:8px">${counts || "Sin citas"}</div>` +
      bars("Ingresos por servicio", b.by_service) +
      bars("Ingresos por profesional", b.by_professional) +
      bars("Ingresos por día", b.by_day, true);
  }
  const st = (k, v) => `<div class="stat"><div class="k">${k}</div><div class="v num">${v}</div></div>`;

  function bars(title, data, isDay) {
    if (!data.length) return `<div class="section-label">${title}</div><div class="empty">Sin ingresos.</div>`;
    const max = Math.max(...data.map((d) => Number(d.revenue)), 0.01);
    const rows = data.map((d) => {
      const w = Math.round((Number(d.revenue) / max) * 100);
      const label = isDay ? Panel.date(d.key + "T00:00:00") : d.key;
      return '<div class="bar-row">' +
        `<div class="lbl">${Panel.esc(label)}</div>` +
        `<div class="bar-track"><div class="bar-fill" style="width:${w}%"></div></div>` +
        `<div class="r num">${Panel.money(d.revenue)}</div></div>`;
    }).join("");
    return `<div class="section-label">${title}</div>${rows}`;
  }

  Panel.onBusiness((b) => { biz = b; if (!document.getElementById("f-body")) shell(); sync(); load(); });
  Panel.mount("facturacion");
})();
