"use strict";
(() => {
  let biz = null, rows = [];

  async function loadList() {
    const c = document.getElementById("content");
    c.innerHTML =
      '<div class="head"><h1>Clientes</h1><div class="sub" id="c-sub"></div>' +
      '<div class="spacer"></div><input id="q" placeholder="Buscar nombre o teléfono"></div>' +
      '<div id="c-body"><div class="skel" style="height:200px"></div></div>';
    document.getElementById("q").oninput = (e) => renderList(e.target.value.trim().toLowerCase());
    try {
      rows = await Panel.api(`/admin/businesses/${biz}/customers`);
      document.getElementById("c-sub").textContent = `${rows.length} cliente(s)`;
      renderList("");
    } catch (e) {
      document.getElementById("c-body").innerHTML = `<div class="err">${Panel.esc(e.message)}</div>`;
    }
  }

  function renderList(filter) {
    const body = document.getElementById("c-body");
    const list = rows.filter((r) =>
      !filter || (r.name || "").toLowerCase().includes(filter) || r.phone.includes(filter));
    if (!list.length) { body.innerHTML = '<div class="card"><div class="empty">Sin clientes que coincidan.</div></div>'; return; }
    body.innerHTML =
      '<div class="card"><table><thead><tr><th>Cliente</th><th>Teléfono</th>' +
      '<th class="r">Citas</th><th class="r">Asistió</th><th class="r">No-show</th>' +
      '<th class="r">Gasto</th><th>Última</th><th></th></tr></thead><tbody>' +
      list.map((r) =>
        `<tr><td><span class="clickable" data-detail="${r.id}"><b>${Panel.esc(r.name || "Sin nombre")}</b></span></td>` +
        `<td class="num">${Panel.esc(r.phone)}</td>` +
        `<td class="r num">${Panel.num(r.total)}</td>` +
        `<td class="r num">${Panel.num(r.completed)}</td>` +
        `<td class="r num">${Panel.num(r.no_shows)}</td>` +
        `<td class="r num">${Panel.money(r.total_spent)}</td>` +
        `<td class="num">${Panel.date(r.last_visit)}</td>` +
        `<td class="r"><button class="btn ghost sm" data-edit="${r.id}">Editar</button></td></tr>`
      ).join("") + "</tbody></table></div>";
    body.querySelectorAll("[data-detail]").forEach((el) =>
      (el.onclick = () => loadDetail(el.dataset.detail)));
    body.querySelectorAll("[data-edit]").forEach((el) =>
      (el.onclick = () => editName(el.dataset.edit)));
  }

  function editName(id) {
    const r = rows.find((x) => x.id === id);
    const name = prompt("Nombre del cliente:", r.name || "");
    if (name === null || name === r.name) return;
    const prev = r.name;
    save(id, name, () => { r.name = prev; renderList(""); save(id, prev); });
    r.name = name; renderList("");
    async function save(cid, value, revert) {
      try {
        await Panel.api(`/admin/businesses/${biz}/customers/${cid}`,
          { method: "PATCH", body: JSON.stringify({ name: value }) });
        if (revert) Panel.undo(`Nombre actualizado`, revert);
      } catch (e) { alert(e.message); }
    }
  }

  async function loadDetail(id) {
    const c = document.getElementById("content");
    c.innerHTML = '<div class="skel" style="height:200px"></div>';
    try {
      const d = await Panel.api(`/admin/businesses/${biz}/customers/${id}`);
      c.innerHTML =
        '<div class="head"><button class="btn sm" id="back">‹ Volver</button>' +
        `<h1 style="margin-left:8px">${Panel.esc(d.name || "Sin nombre")}</h1>` +
        `<div class="sub num">${Panel.esc(d.phone)}</div></div>` +
        '<div class="stats">' +
        st("Citas totales", Panel.num(d.total)) +
        st("Asistió", Panel.num(d.completed)) +
        st("No-shows", Panel.num(d.no_shows)) +
        st("Gasto total", Panel.money(d.total_spent)) +
        st("Última visita", Panel.date(d.last_visit)) +
        "</div>" +
        '<div class="section-label">Historial de citas</div>' +
        (d.appointments.length
          ? '<div class="card"><table><thead><tr><th>Fecha</th><th>Hora</th><th>Servicio</th><th>Profesional</th><th class="r">Precio</th><th>Estado</th></tr></thead><tbody>' +
            d.appointments.map((a) =>
              `<tr class="${a.status === "cancelled" || a.status === "no_show" ? "row-muted" : ""}">` +
              `<td class="num">${Panel.date(a.start_at)}</td><td class="num">${Panel.time(a.start_at)}</td>` +
              `<td>${Panel.esc(a.service_name || "—")}</td><td>${Panel.esc(a.resource_name)}</td>` +
              `<td class="r num">${Panel.money(a.price)}</td><td>${Panel.chip(a.status)}</td></tr>`
            ).join("") + "</tbody></table></div>"
          : '<div class="card"><div class="empty">Sin citas registradas.</div></div>');
      document.getElementById("back").onclick = loadList;
    } catch (e) {
      c.innerHTML = `<div class="err">${Panel.esc(e.message)}</div>`;
    }
  }
  const st = (k, v) => `<div class="stat"><div class="k">${k}</div><div class="v num">${v}</div></div>`;

  Panel.onBusiness((b) => { biz = b; loadList(); });
  Panel.mount("clientes");
})();
