"""Tests del endurecimiento de escalabilidad y seguridad de la Fase 3."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.config import settings
from app.metrics.service import collect_summary
from app.models import (
    Conversation,
    ConversationState,
    EventLog,
    InboundMessage,
)
from app.retention import purge_old


# --------------------------------------------------------------------------- #
#  Agregación SQL: dos modelos y dos días, coste correcto por grupo
# --------------------------------------------------------------------------- #
async def test_summary_multi_model_multi_day(db_session, seed):
    bid = seed.business_id
    old = datetime.now(UTC) - timedelta(days=2)
    db_session.add_all(
        [
            EventLog(business_id=bid, type="llm_call", payload={
                "model": "gpt-4o-mini", "kind": "nlu",
                "prompt_tokens": 1000, "cached_tokens": 0, "completion_tokens": 0}),
            EventLog(business_id=bid, type="llm_call", payload={
                "model": "gpt-4o", "kind": "qa",
                "prompt_tokens": 1000, "cached_tokens": 0, "completion_tokens": 0}),
        ]
    )
    await db_session.flush()
    # Un evento de hace 2 días (otro día en el desglose).
    ev = EventLog(business_id=bid, type="llm_call", payload={
        "model": "gpt-4o-mini", "kind": "nlu",
        "prompt_tokens": 500, "cached_tokens": 0, "completion_tokens": 0})
    db_session.add(ev)
    await db_session.flush()
    ev.created_at = old
    await db_session.commit()

    s = await collect_summary(db_session, business_id=bid, days=30)
    assert s.llm_calls == 3
    # Coste: 1000*0.15 + 1000*2.50 + 500*0.15, todo /1e6.
    expected = (1000 * 0.15 + 1000 * 2.50 + 500 * 0.15) / 1_000_000
    assert abs(s.total_cost_usd - expected) < 1e-9
    assert {m.model for m in s.by_model} == {"gpt-4o-mini", "gpt-4o"}
    # gpt-4o es más caro pese a los mismos tokens → aparece primero.
    assert s.by_model[0].model == "gpt-4o"
    assert len(s.by_day) == 2  # dos días distintos


# --------------------------------------------------------------------------- #
#  Purga de retención
# --------------------------------------------------------------------------- #
async def test_purge_removes_old_and_keeps_recent(db_session, seed):
    bid = seed.business_id
    old = datetime.now(UTC) - timedelta(days=200)

    recent_msg = InboundMessage(business_id=bid, message_id="r1", from_phone="+34600")
    old_msg = InboundMessage(business_id=bid, message_id="o1", from_phone="+34601")
    old_event = EventLog(business_id=bid, type="llm_call", payload={})
    idle_old = Conversation(
        business_id=bid, customer_phone="+34602",
        state=ConversationState.IDLE, context={},
    )
    active_old = Conversation(
        business_id=bid, customer_phone="+34603",
        state=ConversationState.CONFIRMING, context={},
    )
    db_session.add_all([recent_msg, old_msg, old_event, idle_old, active_old])
    await db_session.flush()
    for row in (old_msg, old_event, idle_old, active_old):
        row.created_at = old
        row.updated_at = old
    await db_session.commit()

    result = await purge_old(db_session, days=90)
    assert result["inbound_messages"] == 1
    assert result["events_log"] == 1
    assert result["conversations"] == 1  # solo la IDLE antigua

    remaining_msgs = await db_session.scalar(
        select(func.count()).select_from(InboundMessage)
    )
    assert remaining_msgs == 1  # el reciente sobrevive
    # La conversación activa (CONFIRMING) no se purga aunque sea antigua.
    active = await db_session.scalar(
        select(Conversation).where(Conversation.customer_phone == "+34603")
    )
    assert active is not None


# --------------------------------------------------------------------------- #
#  IP de cliente tras proxy (rate limiting)
# --------------------------------------------------------------------------- #
def test_client_ip_respects_trust_proxy(monkeypatch):
    from app.security import client_ip

    class _Req:
        def __init__(self, xff, peer):
            self.headers = {"x-forwarded-for": xff} if xff else {}
            self.client = type("C", (), {"host": peer})() if peer else None

    # Sin trust_proxy: se ignora XFF (no spoofeable).
    monkeypatch.setattr(settings, "trust_proxy", False)
    assert client_ip(_Req("1.2.3.4, 9.9.9.9", "10.0.0.1")) == "10.0.0.1"

    # Con trust_proxy: se toma la primera IP de XFF.
    monkeypatch.setattr(settings, "trust_proxy", True)
    assert client_ip(_Req("1.2.3.4, 9.9.9.9", "10.0.0.1")) == "1.2.3.4"
    # Con trust_proxy pero sin XFF: cae al peer.
    assert client_ip(_Req(None, "10.0.0.1")) == "10.0.0.1"


# --------------------------------------------------------------------------- #
#  Dashboard: escape XSS defensivo
# --------------------------------------------------------------------------- #
async def test_dashboard_escapes_output(client):
    resp = await client.get("/dashboard")
    assert resp.status_code == 200
    assert "function esc(" in resp.text
    assert "esc(m.model)" in resp.text


# --------------------------------------------------------------------------- #
#  Lock de conversación (serialización de turnos concurrentes en Postgres)
# --------------------------------------------------------------------------- #
def test_conversation_select_locks_on_postgres():
    from sqlalchemy.dialects import postgresql

    from app.agent.flow import _convo_select

    locked = str(
        _convo_select("b", "+34600", lock=True).compile(dialect=postgresql.dialect())
    )
    assert "FOR UPDATE" in locked
    unlocked = str(
        _convo_select("b", "+34600", lock=False).compile(dialect=postgresql.dialect())
    )
    assert "FOR UPDATE" not in unlocked


async def test_load_convo_is_idempotent(db_session, seed):
    from app.agent.flow import _load_convo

    c1 = await _load_convo(db_session, seed.business_id, "+34600999000")
    c2 = await _load_convo(db_session, seed.business_id, "+34600999000")
    assert c1.id == c2.id
    count = await db_session.scalar(
        select(func.count())
        .select_from(Conversation)
        .where(Conversation.customer_phone == "+34600999000")
    )
    assert count == 1
