"use strict";
(() => {
  let biz = null, svcs = [], editing = null;

  async function load() {
    const c = document.getElementById("content");
    c.innerHTML =
      '<div class="head"><h1>Servicios</h1><div class="sub" id="s-sub"></div>' +
      '<div class="spacer"></div>' +
      '<button class="btn primary" id="n-add">+ Nuevo servicio</button></div>' +
      '<div id="s-body"><div class="skel" style="height:160px"></div></div>';
    document.getElementById("n-add").onclick = openAdd;
    try {
      svcs = await Panel.api(`/admin/businesses/${biz}/services?include_inactive=true`);
      render();
    } catch (e) {
      document.getElementById("s-body").innerHTML = `<div class="err">${Panel.esc(e.message)}</div>`;
    }
  }

  function render() {
    document.getElementById("s-sub").textContent = `${svcs.filter((s) => s.active).length} activo(s)`;
    const body = document.getElementById("s-body");
    if (!svcs.length) { body.innerHTML = '<div class="card"><div class="empty">Aún no hay servicios.</div></div>'; return; }
    body.innerHTML =
      '<div class="card"><table><thead><tr><th>Servicio</th><th>Categoría</th>' +
      '<th class="r">Duración</th><th class="r">Precio</th>' +
      '<th class="r" title="Minutos de limpieza/preparación tras la cita">Limpieza</th>' +
      '<th>Estado</th><th></th></tr></thead><tbody>' +
      svcs.map(row).join("") + "</tbody></table></div>";
    wire();
  }

  function row(s) {
    const inactive = s.active ? "" : "row-muted";
    if (editing === s.id) {
      return `<tr class="edited"><td><input class="inline" id="e-name" value="${Panel.esc(s.name)}"></td>` +
        `<td><input class="inline" id="e-cat" value="${Panel.esc(s.category || "")}"></td>` +
        `<td class="r"><input class="inline w-num" id="e-dur" type="number" min="1" value="${s.duration_min}"></td>` +
        `<td class="r"><input class="inline w-num" id="e-price" type="number" min="0" step="0.01" value="${s.price}"></td>` +
        `<td class="r"><input class="inline w-num" id="e-buf" type="number" min="0" step="5" value="${s.buffer_after_min}"></td>` +
        `<td>${estado(s)}</td>` +
        `<td class="r"><button class="btn sm primary" data-save="${s.id}">Guardar</button> <button class="btn ghost sm" data-cancel="1">Cancelar</button></td></tr>`;
    }
    return `<tr class="${inactive}"><td><b>${Panel.esc(s.name)}</b></td>` +
      `<td class="muted">${Panel.esc(canonCategory(s.category))}</td>` +
      `<td class="r num">${s.duration_min} min</td>` +
      `<td class="r num">${Panel.money(s.price)}</td>` +
      `<td class="r num">${s.buffer_after_min ? s.buffer_after_min + " min" : "—"}</td>` +
      `<td>${estado(s)}</td>` +
      `<td class="r"><button class="btn ghost sm" data-ed="${s.id}">Editar</button>` +
      (s.active
        ? `<button class="btn ghost sm" data-del="${s.id}">Baja</button>`
        : `<button class="btn ghost sm" data-on="${s.id}">Reactivar</button>`) +
      "</td></tr>";
  }
  const estado = (s) => s.active
    ? '<span class="chip confirmed">Activo</span>'
    : '<span class="chip pending">De baja</span>';

  function wire() {
    const q = (sel, fn) => document.querySelectorAll(sel).forEach((el) => (el.onclick = () => fn(el)));
    q("[data-ed]", (el) => { editing = el.dataset.ed; render(); });
    q("[data-cancel]", () => { editing = null; render(); });
    q("[data-save]", (el) => saveEdit(el.dataset.save));
    q("[data-del]", (el) => setActive(el.dataset.del, false));
    q("[data-on]", (el) => setActive(el.dataset.on, true));
  }

  async function saveEdit(id) {
    const s = svcs.find((x) => x.id === id);
    const patch = {
      name: document.getElementById("e-name").value.trim(),
      category: document.getElementById("e-cat").value.trim() || null,
      duration_min: Number(document.getElementById("e-dur").value),
      price: document.getElementById("e-price").value,
      buffer_after_min: Number(document.getElementById("e-buf").value) || 0,
    };
    const prev = {
      name: s.name, category: s.category, duration_min: s.duration_min,
      price: s.price, buffer_after_min: s.buffer_after_min,
    };
    try {
      const updated = await Panel.api(`/admin/businesses/${biz}/services/${id}`,
        { method: "PATCH", body: JSON.stringify(patch) });
      Object.assign(s, updated); editing = null; render();
      Panel.undo(`"${s.name}" actualizado`, async () => {
        const back = await Panel.api(`/admin/businesses/${biz}/services/${id}`,
          { method: "PATCH", body: JSON.stringify(prev) });
        Object.assign(s, back); render();
      });
    } catch (e) { alert(e.message); }
  }

  async function setActive(id, active) {
    const s = svcs.find((x) => x.id === id);
    try {
      if (active) {
        const u = await Panel.api(`/admin/businesses/${biz}/services/${id}`,
          { method: "PATCH", body: JSON.stringify({ active: true }) });
        Object.assign(s, u);
      } else {
        await Panel.api(`/admin/businesses/${biz}/services/${id}`, { method: "DELETE" });
        s.active = false;
      }
      render();
      Panel.undo(`"${s.name}" ${active ? "reactivado" : "dado de baja"}`, async () => {
        const u = await Panel.api(`/admin/businesses/${biz}/services/${id}`,
          { method: "PATCH", body: JSON.stringify({ active: !active }) });
        Object.assign(s, u); render();
      });
    } catch (e) { alert(e.message); }
  }

  // Clave de comparación: sin mayúsculas ni acentos ni espacios sobrantes.
  const catKey = (s) => (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "")
    .toLowerCase().trim();
  const BASE_CATS = ["Corte", "Color", "Peinado", "Estética", "Barba", "Uñas", "Depilación"];

  // Opciones del dropdown: categorías del negocio + set base, deduplicadas
  // ignorando mayúsculas/acentos y mostradas en una forma canónica.
  function categoryOptions() {
    const baseByKey = new Map(BASE_CATS.map((c) => [catKey(c), c]));
    const seen = new Map(); // clave normalizada → texto a mostrar
    svcs.forEach((s) => {
      const k = catKey(s.category);
      if (k && !seen.has(k)) seen.set(k, baseByKey.get(k) || Panel.capFirst(s.category.trim()));
    });
    BASE_CATS.forEach((c) => { const k = catKey(c); if (!seen.has(k)) seen.set(k, c); });
    const opts = ['<option value="">Sin categoría</option>'];
    [...seen.values()].sort((a, b) => a.localeCompare(b, "es"))
      .forEach((c) => opts.push(`<option value="${Panel.esc(c)}">${Panel.esc(c)}</option>`));
    opts.push('<option value="__new__">Otra…</option>');
    return opts.join("");
  }

  // Forma canónica de una categoría para mostrar en la tabla (no cambia el dato
  // guardado): unifica mayúsculas/acentos con la del dropdown.
  function canonCategory(cat) {
    if (!cat) return "—";
    const k = catKey(cat);
    return BASE_CATS.find((c) => catKey(c) === k) || Panel.capFirst(cat.trim());
  }

  function openAdd() {
    const f2 = (l, inner) => `<div class="field2"><label>${l}</label>${inner}</div>`;
    const durChips = [15, 30, 45, 60, 90, 120]
      .map((d) => `<button type="button" class="btn sm chip-dur${d === 30 ? " primary" : ""}" data-dur="${d}">${d} min</button>`)
      .join("");
    const mod = Panel.modal({
      title: "Nuevo servicio",
      submitLabel: "Crear servicio",
      html:
        f2("Nombre", '<input id="m-name" placeholder="Ej. Corte de caballero" autocomplete="off">') +
        f2("Categoría",
          `<select id="m-cat">${categoryOptions()}</select>` +
          '<input id="m-cat-new" placeholder="Nueva categoría" autocomplete="off" style="display:none;margin-top:8px">') +
        '<div class="field2"><label>Duración</label>' +
        `<div class="chips-pick">${durChips}</div>` +
        '<input type="number" id="m-dur" min="5" step="5" value="30"></div>' +
        f2("Precio (€)", '<input type="number" id="m-price" min="0" step="0.5" value="0" inputmode="decimal">') +
        '<div class="two">' +
        f2("Preparación antes (min)", '<input type="number" id="m-buf-before" min="0" step="5" value="0">') +
        f2("Limpieza después (min)", '<input type="number" id="m-buf-after" min="0" step="5" value="0">') +
        "</div>" +
        '<div id="m-err" class="err" style="display:none"></div>',
      onSubmit: async (m, close) => {
        const v = (id) => m.querySelector("#" + id).value.trim();
        const err = m.querySelector("#m-err");
        const fail = (msg) => { err.style.display = "block"; err.textContent = msg; };
        let category = v("m-cat");
        if (category === "__new__") category = Panel.capFirst(v("m-cat-new"));
        const body = {
          name: Panel.capFirst(v("m-name")),
          category: category || null,
          duration_min: Number(v("m-dur")),
          price: v("m-price") || "0",
          buffer_before_min: Number(v("m-buf-before")) || 0,
          buffer_after_min: Number(v("m-buf-after")) || 0,
        };
        if (!body.name) return fail("El nombre es obligatorio.");
        if (!(body.duration_min > 0)) return fail("La duración debe ser mayor que 0.");
        try {
          const created = await Panel.api(`/admin/businesses/${biz}/services`,
            { method: "POST", body: JSON.stringify(body) });
          svcs.unshift(created);
          render();
          close();
          Panel.flash(`"${created.name}" añadido`);
        } catch (e) { fail(e.message); }
      },
    });
    // Chips de duración: fijan el valor y resaltan el activo (estilo Revolut).
    const dur = mod.el.querySelector("#m-dur");
    const chips = mod.el.querySelectorAll(".chip-dur");
    chips.forEach((b) => {
      b.onclick = () => {
        chips.forEach((x) => x.classList.remove("primary"));
        b.classList.add("primary");
        dur.value = b.dataset.dur;
      };
    });
    // Si teclean una duración a mano, ningún chip queda resaltado.
    dur.oninput = () => chips.forEach((x) => {
      if (x.dataset.dur !== dur.value) x.classList.remove("primary");
      else x.classList.add("primary");
    });
    // "Otra…" revela el campo para crear una categoría nueva.
    const catSel = mod.el.querySelector("#m-cat");
    const catNew = mod.el.querySelector("#m-cat-new");
    catSel.onchange = () => {
      const isNew = catSel.value === "__new__";
      catNew.style.display = isNew ? "block" : "none";
      if (isNew) catNew.focus();
    };
    // Primer carácter en mayúscula en vivo (nombre y categoría nueva).
    Panel.autoCapFirst(mod.el.querySelector("#m-name"));
    Panel.autoCapFirst(catNew);
    mod.el.querySelector("#m-name").focus();
  }

  Panel.onBusiness((b) => { biz = b; editing = null; load(); });
  Panel.mount("servicios");
})();
