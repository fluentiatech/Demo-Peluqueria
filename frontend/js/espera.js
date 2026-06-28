"use strict";
(() => {
  let biz = null, data = null, services = null, resources = null;

  const svcName = (id) => { const s = (services || []).find((x) => x.id === id); return s ? s.name : "—"; };
  const resName = (id) => { if (!id) return "Cualquier profesional"; const r = (resources || []).find((x) => x.id === id); return r ? r.name : "—"; };

  async function load() {
    const c = document.getElementById("content");
    c.innerHTML =
      '<div class="head"><h1>Lista de espera</h1><div class="sub" id="w-sub"></div>' +
      '<div class="spacer"></div><button class="btn primary" id="w-add">+ Añadir a espera</button></div>' +
      '<div id="w-body"><div class="skel" style="height:160px"></div></div>';
    document.getElementById("w-add").onclick = openAdd;
    document.getElementById("w-body").addEventListener("click", onClick);
    try {
      [data, services, resources] = await Promise.all([
        Panel.api(`/admin/businesses/${biz}/waitlist`),
        Panel.api(`/admin/businesses/${biz}/services`),
        Panel.api(`/admin/businesses/${biz}/resources`),
      ]);
      render();
    } catch (e) {
      document.getElementById("w-body").innerHTML = `<div class="err">${Panel.esc(e.message)}</div>`;
    }
  }

  function render() {
    document.getElementById("w-sub").textContent = `${data.length} en espera`;
    const body = document.getElementById("w-body");
    if (!data.length) {
      body.innerHTML = '<div class="card"><div class="empty">Nadie en lista de espera.<br>' +
        'Cuando no hay hueco, el agente ofrece apuntarse aquí y avisa solo al liberarse uno.</div></div>';
      return;
    }
    body.innerHTML = '<div class="card">' + data.map(row).join("") + "</div>";
  }

  function row(e) {
    const when = e.desired_date
      ? new Date(e.desired_date + "T00:00:00").toLocaleDateString("es-ES", { weekday: "short", day: "numeric", month: "short" })
      : "Cualquier día";
    const st = e.status === "notified"
      ? '<span class="chip confirmed">Avisado</span>'
      : '<span class="chip pending">Esperando</span>';
    return '<div class="appt"><div class="who">' +
      `<div class="name">${Panel.esc(e.customer_name || "—")} · ${Panel.esc(e.customer_phone)}</div>` +
      `<div class="meta">${Panel.esc(svcName(e.service_id))} · ${Panel.esc(resName(e.resource_id))} · ${when}</div></div>` +
      `${st}<button class="btn ghost sm danger" data-del="${e.id}">Quitar</button></div>`;
  }

  function onClick(ev) {
    const d = ev.target.closest("[data-del]");
    if (d) remove(d.dataset.del);
  }

  async function remove(id) {
    if (!confirm("¿Quitar de la lista de espera?")) return;
    try {
      await Panel.api(`/admin/businesses/${biz}/waitlist/${id}`, { method: "DELETE" });
      data = data.filter((x) => x.id !== id);
      render();
      Panel.flash("Quitado de la espera");
    } catch (e) { alert(e.message); }
  }

  function openAdd() {
    const opt = (arr) => arr.map((x) => `<option value="${Panel.esc(x.id)}">${Panel.esc(x.name)}</option>`).join("");
    const mod = Panel.modal({
      title: "Añadir a lista de espera",
      submitLabel: "Añadir",
      html:
        `<div class="field2"><label>Servicio</label><select id="m-svc">${opt(services)}</select></div>` +
        '<div class="field2"><label>Profesional</label>' +
        `<select id="m-res"><option value="">Cualquiera</option>${opt(resources)}</select></div>` +
        '<div class="field2"><label>Día deseado (opcional)</label><input type="date" id="m-date"></div>' +
        '<div class="field2"><label>Cliente</label><input id="m-name" placeholder="Nombre"></div>' +
        '<div class="field2"><label>Teléfono</label><input id="m-phone" placeholder="+34600111222"></div>' +
        '<div id="m-err" class="err" style="display:none"></div>',
      onSubmit: async (m, close) => {
        const v = (id) => m.querySelector("#" + id).value.trim();
        const err = m.querySelector("#m-err");
        try {
          const created = await Panel.api(`/admin/businesses/${biz}/waitlist`, {
            method: "POST",
            body: JSON.stringify({
              service_id: v("m-svc"),
              resource_id: v("m-res") || null,
              desired_date: v("m-date") || null,
              customer: { phone: v("m-phone"), name: Panel.capFirst(v("m-name")) || null },
            }),
          });
          data.unshift(created);
          render();
          close();
          Panel.flash("Añadido a la espera");
        } catch (e) { err.style.display = "block"; err.textContent = e.message; }
      },
    });
    Panel.autoCapFirst(mod.el.querySelector("#m-name"));
  }

  Panel.onBusiness((b) => { biz = b; services = resources = null; load(); });
  Panel.mount("espera");
})();
