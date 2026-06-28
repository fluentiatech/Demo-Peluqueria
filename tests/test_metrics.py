"""Tests de la observabilidad de coste: cálculo, agregación y API."""
from __future__ import annotations

from app.config import settings
from app.metrics.cost import cost_usd
from app.metrics.service import collect_summary
from app.models import EventLog, InboundMessage


# --------------------------------------------------------------------------- #
#  Cálculo de coste
# --------------------------------------------------------------------------- #
def test_cost_basic():
    # gpt-4o-mini: input 0.15, output 0.60 por 1M.
    c = cost_usd("gpt-4o-mini", prompt_tokens=1000, cached_tokens=0, completion_tokens=1000)
    assert abs(c - (1000 * 0.15 + 1000 * 0.60) / 1_000_000) < 1e-12


def test_cost_cached_split():
    # 600 no cacheados a 0.15 + 400 cacheados a 0.075.
    c = cost_usd("gpt-4o-mini", prompt_tokens=1000, cached_tokens=400, completion_tokens=0)
    assert abs(c - (600 * 0.15 + 400 * 0.075) / 1_000_000) < 1e-12


def test_cost_unknown_model_uses_fallback():
    assert cost_usd("modelo-raro", 1000, 0, 0) > 0


# --------------------------------------------------------------------------- #
#  Agregación
# --------------------------------------------------------------------------- #
async def test_collect_summary_aggregates(db_session, seed):
    bid = seed.business_id
    db_session.add_all(
        [
            EventLog(
                business_id=bid,
                type="llm_call",
                payload={
                    "model": "gpt-4o-mini", "kind": "nlu",
                    "prompt_tokens": 1000, "cached_tokens": 0, "completion_tokens": 200,
                },
            ),
            EventLog(
                business_id=bid,
                type="llm_call",
                payload={
                    "model": "gpt-4o-mini", "kind": "qa",
                    "prompt_tokens": 500, "cached_tokens": 100, "completion_tokens": 50,
                },
            ),
            EventLog(business_id=bid, type="handoff", payload={}),
            EventLog(business_id=bid, type="error", payload={}),
            InboundMessage(business_id=bid, message_id="wamid.1", from_phone="+34600"),
        ]
    )
    await db_session.commit()

    s = await collect_summary(db_session, business_id=bid, days=30)
    assert s.llm_calls == 2
    assert s.prompt_tokens == 1500
    assert s.cached_tokens == 100
    assert s.completion_tokens == 250
    assert s.total_cost_usd > 0
    assert {k.kind for k in s.by_kind} == {"nlu", "qa"}
    assert s.by_model[0].model == "gpt-4o-mini"
    assert s.handoffs == 1
    assert s.errors == 1
    assert s.inbound_messages == 1
    assert s.conversations == 1
    assert s.cost_per_conversation_usd == s.total_cost_usd  # 1 conversación


async def test_summary_empty_is_safe(db_session, seed):
    s = await collect_summary(db_session, business_id=seed.business_id, days=30)
    assert s.llm_calls == 0
    assert s.total_cost_usd == 0
    assert s.cost_per_conversation_usd == 0


# --------------------------------------------------------------------------- #
#  API + dashboard
# --------------------------------------------------------------------------- #
async def test_metrics_endpoint(client, seed):
    # Una consulta /ask genera un evento llm_call.
    await client.post(
        f"/admin/businesses/{seed.business_id}/ask", json={"message": "hola"}
    )
    resp = await client.get("/admin/metrics/summary?days=30")
    assert resp.status_code == 200
    assert resp.json()["llm_calls"] >= 1


async def test_metrics_requires_api_key_when_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_api_key", "secreta")
    resp = await client.get("/admin/metrics/summary")
    assert resp.status_code == 401


async def test_dashboard_html_served_without_auth(client):
    resp = await client.get("/dashboard")
    assert resp.status_code == 200
    assert "Panel de coste" in resp.text
