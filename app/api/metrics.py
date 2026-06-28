"""API de métricas de coste + dashboard HTML.

- `router` (prefijo /admin): JSON con el resumen, protegido por API key.
- `ui_router`: el dashboard HTML (sin datos; pide la API key y consulta el JSON).
  Se sirve con una **CSP basada en nonce** y SIN dependencias de CDN externas
  (gráfico dibujado en el cliente), para eliminar XSS y riesgo de cadena de
  suministro. Solo se publica si `dashboard_on` (no en producción por defecto).
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.metrics.service import collect_summary
from app.schemas.metrics import MetricsSummary

router = APIRouter(prefix="/admin", tags=["metrics"])
ui_router = APIRouter(tags=["metrics"])


@router.get("/metrics/summary", response_model=MetricsSummary)
async def metrics_summary(
    business_id: str | None = None,
    days: int = Query(30, ge=1, le=3650),
    session: AsyncSession = Depends(get_session),
) -> MetricsSummary:
    days = min(days, settings.metrics_max_days)
    return await collect_summary(session, business_id=business_id, days=days)


@ui_router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard() -> HTMLResponse:
    nonce = secrets.token_urlsafe(16)
    html = _DASHBOARD_HTML.replace("__NONCE__", nonce)
    csp = (
        "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
        "object-src 'none'; img-src 'self'; connect-src 'self'; "
        f"style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'"
    )
    return HTMLResponse(html, headers={"Content-Security-Policy": csp})


# Nota: el gráfico se dibuja con elementos DOM (sin librerías externas) y las
# alturas se fijan vía CSSOM, compatible con la CSP estricta de nonce.
_DASHBOARD_HTML = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Panel de coste · Agente de Citas</title>
<style nonce="__NONCE__">
  :root { --bg:#0f172a; --card:#1e293b; --txt:#e2e8f0; --muted:#94a3b8; --accent:#38bdf8; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:system-ui,Segoe UI,Roboto,sans-serif; background:var(--bg); color:var(--txt); }
  header { padding:18px 24px; border-bottom:1px solid #334155; }
  h1 { margin:0; font-size:18px; }
  .controls { display:flex; gap:8px; flex-wrap:wrap; padding:16px 24px; align-items:end; }
  .controls label { display:flex; flex-direction:column; font-size:12px; color:var(--muted); gap:4px; }
  input, button { padding:8px 10px; border-radius:8px; border:1px solid #334155; background:#0b1220; color:var(--txt); }
  button { background:var(--accent); color:#04222e; font-weight:600; cursor:pointer; border:none; }
  main { padding:8px 24px 32px; }
  .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; }
  .card { background:var(--card); border:1px solid #334155; border-radius:12px; padding:14px; }
  .card .k { font-size:12px; color:var(--muted); }
  .card .v { font-size:22px; font-weight:700; margin-top:6px; }
  .chart { display:flex; align-items:flex-end; gap:4px; height:140px; background:var(--card); border-radius:12px; padding:12px; margin-top:16px; overflow-x:auto; }
  .chart .bar { display:flex; flex-direction:column; align-items:center; justify-content:flex-end; gap:4px; min-width:26px; height:100%; }
  .chart .fill { width:18px; background:var(--accent); border-radius:4px 4px 0 0; }
  .chart .lbl { font-size:10px; color:var(--muted); }
  table { width:100%; border-collapse:collapse; margin-top:16px; background:var(--card); border-radius:12px; overflow:hidden; }
  th, td { text-align:left; padding:10px 12px; border-bottom:1px solid #334155; font-size:14px; }
  th { color:var(--muted); font-weight:600; }
  .err { color:#fca5a5; padding:12px 24px; }
  .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
  @media (max-width:720px){ .grid2{ grid-template-columns:1fr; } }
</style>
</head>
<body>
<header><h1>Panel de coste — Agente de Citas</h1></header>
<div class="controls">
  <label>API key<input id="key" type="password" placeholder="X-API-Key"></label>
  <label>Business ID (opcional)<input id="biz" type="text" placeholder="todos"></label>
  <label>Días<input id="days" type="number" value="30" min="1"></label>
  <button id="go">Cargar</button>
</div>
<div id="error" class="err"></div>
<main id="main" hidden>
  <div class="cards" id="cards"></div>
  <div class="chart" id="chart"></div>
  <div class="grid2">
    <table id="byModel"><thead><tr><th>Modelo</th><th>Llamadas</th><th>Tokens</th><th>Coste $</th></tr></thead><tbody></tbody></table>
    <table id="byKind"><thead><tr><th>Tipo</th><th>Llamadas</th><th>Coste $</th></tr></thead><tbody></tbody></table>
  </div>
</main>
<script nonce="__NONCE__">
function esc(s){ return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function card(k, v){ return '<div class="card"><div class="k">'+esc(k)+'</div><div class="v">'+esc(v)+'</div></div>'; }
function fmt(n){ return Number(n).toLocaleString('es-ES'); }
function usd(n){ return '$'+Number(n).toFixed(4); }
function drawChart(byDay){
  const wrap = document.getElementById('chart');
  wrap.textContent = '';
  const max = Math.max(0.000001, ...byDay.map(x => x.cost_usd));
  byDay.forEach(x => {
    const bar = document.createElement('div'); bar.className = 'bar';
    bar.title = x.date + ': ' + usd(x.cost_usd);
    const fill = document.createElement('div'); fill.className = 'fill';
    fill.style.height = Math.round((x.cost_usd / max) * 100) + '%';
    const lbl = document.createElement('span'); lbl.className = 'lbl';
    lbl.textContent = x.date.slice(5);
    bar.appendChild(fill); bar.appendChild(lbl); wrap.appendChild(bar);
  });
}
async function load(){
  const key = document.getElementById('key').value.trim();
  const biz = document.getElementById('biz').value.trim();
  const days = document.getElementById('days').value || 30;
  const err = document.getElementById('error');
  err.textContent = '';
  const url = '/admin/metrics/summary?days='+encodeURIComponent(days) + (biz ? '&business_id='+encodeURIComponent(biz) : '');
  let resp;
  try { resp = await fetch(url, { headers: key ? {'X-API-Key': key} : {} }); }
  catch(e){ err.textContent = 'Error de red'; return; }
  if(!resp.ok){ err.textContent = 'Error '+resp.status+' (¿API key correcta?)'; return; }
  const d = await resp.json();
  document.getElementById('main').hidden = false;
  document.getElementById('cards').innerHTML =
    card('Coste total', usd(d.total_cost_usd)) +
    card('Coste / conversación', usd(d.cost_per_conversation_usd)) +
    card('Llamadas LLM', fmt(d.llm_calls)) +
    card('Tokens entrada', fmt(d.prompt_tokens)) +
    card('Tokens cacheados', fmt(d.cached_tokens)) +
    card('Tokens salida', fmt(d.completion_tokens)) +
    card('Conversaciones', fmt(d.conversations)) +
    card('Mensajes entrantes', fmt(d.inbound_messages)) +
    card('Citas creadas', fmt(d.appointments_created)) +
    card('Turnos sin LLM', fmt(d.prefiltered_turns)) +
    card('Handoffs', fmt(d.handoffs)) +
    card('Errores', fmt(d.errors));
  drawChart(d.by_day);
  document.querySelector('#byModel tbody').innerHTML = d.by_model.map(m =>
    '<tr><td>'+esc(m.model)+'</td><td>'+fmt(m.calls)+'</td><td>'+fmt(m.prompt_tokens+m.completion_tokens)+'</td><td>'+usd(m.cost_usd)+'</td></tr>').join('');
  document.querySelector('#byKind tbody').innerHTML = d.by_kind.map(k =>
    '<tr><td>'+esc(k.kind)+'</td><td>'+fmt(k.calls)+'</td><td>'+usd(k.cost_usd)+'</td></tr>').join('');
}
document.getElementById('go').addEventListener('click', load);
</script>
</body>
</html>"""
