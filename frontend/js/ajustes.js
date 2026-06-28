"use strict";
(() => {
  let biz = null, b = null;

  async function load() {
    const c = document.getElementById("content");
    c.innerHTML = '<div class="skel" style="height:440px"></div>';
    try { b = await Panel.api(`/admin/businesses/${biz}`); render(); }
    catch (e) { c.innerHTML = `<div class="err">${Panel.esc(e.message)}</div>`; }
  }

  const F2 = (l, inner, hint) =>
    `<div class="field2"><label>${l}</label>${inner}` +
    (hint ? `<div class="muted" style="font-size:12px">${hint}</div>` : "") + "</div>";

  const seg = (id, val, opts) =>
    `<div class="seg" data-seg="${id}">` +
    opts.map(([v, lab]) => `<button data-v="${v}" class="${String(v) === String(val) ? "on" : ""}">${lab}</button>`).join("") +
    "</div>";

  const langOpts = [["auto", "Automático (el del cliente)"], ["es", "Español"], ["ca", "Català"], ["en", "English"]];

  function render() {
    const color = /^#[0-9a-fA-F]{6}$/.test(b.brand_color || "") ? b.brand_color : "#0a0a0b";
    document.getElementById("content").innerHTML =
      '<div class="head"><h1>Ajustes</h1><div class="sub">Personaliza tu recepcionista y la marca del panel.</div>' +
      '<div class="spacer"></div><button class="btn primary" id="save">Guardar cambios</button></div>' +

      '<div class="card"><div class="card-head"><h3>Tu recepcionista virtual</h3></div><div class="pad">' +
      F2("Nombre del asistente", `<input id="f-name" maxlength="40" value="${Panel.esc(b.assistant_name || "")}" placeholder="Ej. Lucía">`, "Se presenta así al saludar por WhatsApp.") +
      F2("Tono", seg("tone", b.agent_tone, [["cercano", "Cercano"], ["formal", "Formal"]])) +
      F2("Emojis", seg("emo", b.use_emojis, [[true, "Sí"], [false, "No"]])) +
      F2("Idioma", '<select id="f-lang">' + langOpts.map(([v, l]) => `<option value="${v}" ${b.agent_language === v ? "selected" : ""}>${l}</option>`).join("") + "</select>") +
      '<div class="field2"><label>Vista previa del saludo</label><div id="preview" class="prev"></div></div>' +
      "</div></div>" +

      '<div class="card"><div class="card-head"><h3>Marca del panel</h3></div><div class="pad">' +
      F2("Nombre del negocio", `<input id="f-biz" maxlength="160" value="${Panel.esc(b.name || "")}">`) +
      '<div class="two">' +
      F2("Color de acento", `<input type="color" id="f-color" value="${color}" style="height:44px;padding:4px">`) +
      F2("Logo (URL)", `<input id="f-logo" maxlength="300" value="${Panel.esc(b.logo_url || "")}" placeholder="https://…/logo.png">`) +
      "</div></div></div>" +

      '<div class="card"><div class="card-head"><h3>Notas y políticas para el agente</h3></div><div class="pad">' +
      F2("Texto libre", `<textarea id="f-ctx" rows="5" maxlength="8000" placeholder="Ej. política de cancelación, promociones, indicaciones especiales…">${Panel.esc(b.system_context || "")}</textarea>`, "Se añade al contexto del agente (lo tiene en cuenta al responder).") +
      "</div></div>";

    b._tone = b.agent_tone;
    b._emo = b.use_emojis;
    wire();
    preview();
  }

  function wire() {
    document.getElementById("save").onclick = save;
    document.querySelectorAll(".seg").forEach((s) => {
      s.querySelectorAll("button").forEach((btn) => {
        btn.onclick = () => {
          s.querySelectorAll("button").forEach((x) => x.classList.remove("on"));
          btn.classList.add("on");
          if (s.dataset.seg === "tone") b._tone = btn.dataset.v;
          if (s.dataset.seg === "emo") b._emo = btn.dataset.v === "true";
          preview();
        };
      });
    });
    const name = document.getElementById("f-name");
    name.oninput = preview;
    Panel.autoCapFirst(name);
    Panel.autoCapFirst(document.getElementById("f-biz"));
  }

  function preview() {
    const name = document.getElementById("f-name").value.trim();
    const emo = b._emo ? "👋 " : "";
    const intro = name ? ` Soy ${name}.` : "";
    document.getElementById("preview").textContent =
      `${emo}¡Hola! (o ¡Hola, María! si te conoce).${intro} ¿Quieres pedir cita o tienes alguna duda?`;
  }

  async function save() {
    const g = (id) => document.getElementById(id).value;
    const payload = {
      name: Panel.capFirst(g("f-biz").trim()) || b.name,
      assistant_name: g("f-name").trim() || null,
      agent_tone: b._tone,
      use_emojis: b._emo,
      agent_language: g("f-lang"),
      brand_color: g("f-color"),
      logo_url: g("f-logo").trim() || null,
      system_context: g("f-ctx").trim() || null,
    };
    const btn = document.getElementById("save");
    btn.disabled = true;
    try {
      const updated = await Panel.api(`/admin/businesses/${biz}`,
        { method: "PATCH", body: JSON.stringify(payload) });
      b = updated;
      Panel.setBusiness(updated); // refresca marca + selector
      render();
      Panel.flash("Ajustes guardados");
    } catch (e) {
      alert(e.message);
      const bb = document.getElementById("save");
      if (bb) bb.disabled = false;
    }
  }

  Panel.onBusiness((x) => { biz = x; load(); });
  Panel.mount("ajustes");
})();
