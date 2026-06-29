"use strict";
(() => {
  let biz = null, curDate = Panel.todayISO(), data = null;
  let services = null, resources = null; // para el alta manual

  function shell() {
    document.getElementById("content").innerHTML =
      '<div class="head"><h1>Agenda</h1><div class="sub" id="ag-sub"></div>' +
      '<div class="spacer"></div>' +
      '<button class="btn sm" id="prev">‹</button>' +
      `<input type="date" id="date" value="${curDate}">` +
      '<button class="btn sm" id="next">›</button>' +
      '<button class="btn sm" id="today">Hoy</button>' +
      '<button class="btn primary" id="add">+ Nueva cita</button></div>' +
      '<div id="ag-body"></div>';
    document.getElementById("date").onchange = (e) => { curDate = e.target.value; load(); };
    document.getElementById("prev").onclick = () => shiftDay(-1);
    document.getElementById("next").onclick = () => shiftDay(1);
    document.getElementById("today").onclick = () => { curDate = Panel.todayISO(); sync(); load(); };
    document.getElementById("add").onclick = openAdd;
    document.getElementById("ag-body").addEventListener("click", onClick);
  }

  const toISO = (d) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

  function shiftDay(n) {
    const d = new Date(curDate + "T00:00:00");
    d.setDate(d.getDate() + n);
    curDate = toISO(d); // componentes locales: evita el desfase UTC de toISOString
    sync(); load();
  }
  const sync = () => { const el = document.getElementById("date"); if (el) el.value = curDate; };

  async function load() {
    const body = document.getElementById("ag-body");
    body.innerHTML = '<div class="skel" style="height:160px"></div>';
    try {
      data = await Panel.api(`/admin/businesses/${biz}/agenda?date=${curDate}`);
      render();
    } catch (e) { body.innerHTML = `<div class="err">${Panel.esc(e.message)}</div>`; }
  }

  function seg(it) {
    // "Asistió"/"No vino" solo cuando ya ha pasado la hora de inicio de la cita.
    const t = it.start_at ? new Date(it.start_at).getTime() : 0;
    const started = !t || isNaN(t) || t <= Date.now();
    return '<div class="seg" data-id="' + it.id + '">' +
      Panel.STATUS_ORDER.map((s) => {
        const locked = !started && (s === "completed" || s === "no_show");
        const cls = (it.status === s ? "on" : "") + (locked ? " locked" : "");
        const ttl = locked ? ` title="Podrás marcar la asistencia a partir de las ${Panel.time(it.start_at)}"` : "";
        return `<button data-st="${s}" class="${cls}"${locked ? " disabled" : ""}${ttl}>${Panel.STATUS[s]}</button>`;
      }).join("") + "</div>";
  }

  function render() {
    document.getElementById("ag-sub").textContent =
      new Date(curDate + "T00:00:00").toLocaleDateString("es-ES", { weekday: "long", day: "numeric", month: "long" });
    const body = document.getElementById("ag-body");
    if (!data.items.length) {
      body.innerHTML = '<div class="card"><div class="empty">No hay citas este día.<br>Pulsa «+ Nueva cita» para añadir una.</div></div>';
      return;
    }
    const groups = data.resources.map((r) => [r, data.items.filter((i) => i.resource_id === r.id)]);
    const known = new Set(data.resources.map((r) => r.id));
    const others = data.items.filter((i) => !known.has(i.resource_id));
    if (others.length) groups.push([{ name: "Otros" }, others]);

    body.innerHTML = groups.filter(([, its]) => its.length).map(([r, its]) =>
      '<div class="card"><div class="card-head"><h3>' + Panel.esc(r.name) +
      `</h3><span class="count">· ${its.length} cita(s)</span></div>` +
      its.map((it) => {
        const off = it.status === "no_show" ? "is-off" : "";
        return `<div class="appt ${off}">` +
          `<div class="time">${Panel.time(it.start_at)}</div>` +
          `<div class="who"><div class="name">${Panel.esc(it.customer_name || "—")}</div>` +
          `<div class="meta">${Panel.esc(it.service_name || "—")} · ${Panel.esc(it.customer_phone)}</div></div>` +
          `<div class="price">${Panel.money(it.price)}</div>` +
          seg(it) +
          `<button class="btn ghost sm danger" data-del="${it.id}">Eliminar</button></div>`;
      }).join("") + "</div>"
    ).join("");
  }

  async function onClick(e) {
    const del = e.target.closest("[data-del]");
    if (del) return remove(del.dataset.del);
    const segBtn = e.target.closest(".seg button");
    if (!segBtn || segBtn.disabled) return;
    const id = segBtn.closest(".seg").dataset.id;
    const it = data.items.find((i) => i.id === id);
    if (!it || it.status === segBtn.dataset.st) return;
    const prev = it.status, next = segBtn.dataset.st;

    // Si la asistencia YA estaba marcada (Asistió/No vino), cambiarla pide
    // confirmación explícita en un popup; el primer marcado es directo.
    if (prev === "completed" || prev === "no_show") {
      Panel.modal({
        title: "Cambiar asistencia",
        submitLabel: "Sí, cambiar",
        html: `<p class="confirm-txt">Esta cita está marcada como <b>${Panel.STATUS[prev]}</b>.<br><br>` +
              `¿Seguro que quieres cambiarla a <b>${Panel.STATUS[next]}</b>?</p>`,
        onSubmit: async (m, close) => {
          await setStatus(it, next);
          close();
          Panel.flash(`Cambiada a ${Panel.STATUS[next]}`);
        },
      });
      return;
    }

    await setStatus(it, next);
    Panel.undo(`${it.customer_name || "Cliente"}: ${Panel.STATUS[next]}`, () => setStatus(it, prev));
  }

  async function setStatus(it, status) {
    await Panel.api(`/admin/businesses/${biz}/appointments/${it.id}/status`,
      { method: "POST", body: JSON.stringify({ status }) });
    it.status = status; render();
  }

  async function remove(id) {
    const it = data.items.find((i) => i.id === id);
    if (!confirm(`¿Eliminar la cita de ${it ? it.customer_name : "este cliente"}? Se libera el hueco.`)) return;
    try {
      await Panel.api(`/admin/businesses/${biz}/appointments/${id}/cancel`, { method: "POST" });
      data.items = data.items.filter((i) => i.id !== id);
      render();
      Panel.flash("Cita eliminada");
    } catch (e) { alert(e.message); }
  }

  async function openAdd() {
    if (!services || !resources) {
      [services, resources] = await Promise.all([
        Panel.api(`/admin/businesses/${biz}/services`),
        Panel.api(`/admin/businesses/${biz}/resources`),
      ]);
    }
    const opts = (arr, val, lab) => arr.map((x) => `<option value="${Panel.esc(x[val])}">${Panel.esc(x[lab])}</option>`).join("");
    const mod = Panel.modal({
      title: "Nueva cita",
      submitLabel: "Crear cita",
      html:
        `<div class="field2"><label>Profesional</label><select id="m-res">${opts(resources, "id", "name")}</select></div>` +
        `<div class="field2"><label>Servicio</label><select id="m-svc">${opts(services, "id", "name")}</select></div>` +
        '<div class="two"><div class="field2"><label>Fecha</label>' +
        `<input type="date" id="m-date" value="${curDate}"></div>` +
        '<div class="field2"><label>Hora</label><input type="time" id="m-time" value="10:00"></div></div>' +
        '<div class="field2"><label>Cliente</label><input id="m-name" placeholder="Nombre"></div>' +
        '<div class="field2"><label>Teléfono</label><input id="m-phone" placeholder="+34600111222"></div>' +
        '<div id="m-err" class="err" style="display:none"></div>',
      onSubmit: async (m, close) => {
        const v = (id) => m.querySelector("#" + id).value.trim();
        const err = m.querySelector("#m-err");
        const start = `${v("m-date")}T${v("m-time")}:00`;
        try {
          await Panel.api(`/admin/businesses/${biz}/appointments`, {
            method: "POST",
            body: JSON.stringify({
              service_id: v("m-svc"), resource_id: v("m-res"), start_at: start,
              customer: { phone: v("m-phone"), name: Panel.capFirst(v("m-name")) || null },
              force: true,
            }),
          });
          close();
          if (v("m-date") === curDate) load();
          Panel.flash("Cita creada");
        } catch (e) {
          err.style.display = "block";
          err.textContent = e.message;
        }
      },
    });
    Panel.autoCapFirst(mod.el.querySelector("#m-name")); // primer carácter en mayúscula
  }

  Panel.onBusiness((b) => { biz = b; services = resources = null; if (!document.getElementById("ag-body")) shell(); load(); });
  Panel.mount("agenda");
})();
