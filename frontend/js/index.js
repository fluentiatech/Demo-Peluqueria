"use strict";
Panel.onBusiness(async (biz) => {
  const c = document.getElementById("content");
  c.innerHTML =
    '<div class="head"><h1>Resumen de hoy</h1><div class="sub" id="today"></div></div>' +
    '<div class="stats" id="stats">' +
    '<div class="stat"><div class="k">Citas hoy</div><div class="v skel"></div></div>'.repeat(4) +
    "</div>" +
    '<div class="section-label">Accesos</div>' +
    '<table><tbody>' +
    '<tr class="clickable" onclick="location.href=\'/panel/agenda.html\'"><td><b>Agenda</b></td><td class="muted">Citas del día por profesional · marcar asistencia</td></tr>' +
    '<tr class="clickable" onclick="location.href=\'/panel/clientes.html\'"><td><b>Clientes</b></td><td class="muted">Histórico, gasto y fidelidad por cliente</td></tr>' +
    '<tr class="clickable" onclick="location.href=\'/panel/servicios.html\'"><td><b>Servicios</b></td><td class="muted">Editar duración y precio · alta y baja</td></tr>' +
    '<tr class="clickable" onclick="location.href=\'/panel/facturacion.html\'"><td><b>Facturación</b></td><td class="muted">Ingresos por periodo, servicio y profesional</td></tr>' +
    "</tbody></table>";

  const today = Panel.todayISO();
  document.getElementById("today").textContent = new Date().toLocaleDateString("es-ES",
    { weekday: "long", day: "numeric", month: "long", year: "numeric" });

  const firstOfMonth = today.slice(0, 8) + "01";
  try {
    const [ag, bill] = await Promise.all([
      Panel.api(`/admin/businesses/${biz}/agenda?date=${today}`),
      Panel.api(`/admin/businesses/${biz}/billing?date_from=${firstOfMonth}&date_to=${today}`),
    ]);
    const done = ag.items.filter((i) => i.status === "completed").length;
    const pend = ag.items.filter((i) => i.status === "pending" || i.status === "confirmed").length;
    document.getElementById("stats").innerHTML =
      stat("Citas hoy", Panel.num(ag.items.length)) +
      stat("Pendientes hoy", Panel.num(pend)) +
      stat("Atendidas hoy", Panel.num(done)) +
      stat("Facturado este mes", Panel.money(bill.revenue_billed));
  } catch (e) {
    document.getElementById("stats").innerHTML = `<div class="err">${Panel.esc(e.message)}</div>`;
  }
});
function stat(k, v) {
  return `<div class="stat"><div class="k">${k}</div><div class="v num">${v}</div></div>`;
}
Panel.mount("");
