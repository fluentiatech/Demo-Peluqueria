"""Tests de fiabilidad/operación (Fase 4): reintentos, alertas y bandeja de handoff."""
from __future__ import annotations

import httpx

from app.config import settings
from app.integrations import whatsapp
from app.models import Business, Conversation, ConversationState, EventLog
from app.notifications import send_pending_alerts


# --------------------------------------------------------------------------- #
#  Reintentos de envío con backoff
# --------------------------------------------------------------------------- #
class _Resp:
    def __init__(self, code: int) -> None:
        self.status_code = code
        self.text = "x"


def _fake_client(codes: list[int], calls: dict):
    class _Client:
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, *_a, **_k):
            i = calls["n"]
            calls["n"] += 1
            return _Resp(codes[min(i, len(codes) - 1)])

    return _Client


async def test_send_retries_transient_then_succeeds(monkeypatch):
    monkeypatch.setattr(settings, "send_retry_backoff_s", 0)
    monkeypatch.setattr(settings, "send_max_retries", 3)
    calls = {"n": 0}
    monkeypatch.setattr(whatsapp.httpx, "AsyncClient", _fake_client([503, 200], calls))

    assert await whatsapp._post_message("123", {}) is True
    assert calls["n"] == 2  # reintentó una vez


async def test_send_does_not_retry_permanent_4xx(monkeypatch):
    monkeypatch.setattr(settings, "send_retry_backoff_s", 0)
    monkeypatch.setattr(settings, "send_max_retries", 3)
    calls = {"n": 0}
    monkeypatch.setattr(whatsapp.httpx, "AsyncClient", _fake_client([400], calls))

    assert await whatsapp._post_message("123", {}) is False
    assert calls["n"] == 1  # no reintenta un error permanente


async def test_send_gives_up_after_max_retries(monkeypatch):
    monkeypatch.setattr(settings, "send_retry_backoff_s", 0)
    monkeypatch.setattr(settings, "send_max_retries", 3)
    calls = {"n": 0}
    monkeypatch.setattr(whatsapp.httpx, "AsyncClient", _fake_client([500], calls))

    assert await whatsapp._post_message("123", {}) is False
    assert calls["n"] == 3


async def test_send_retries_on_network_error(monkeypatch):
    monkeypatch.setattr(settings, "send_retry_backoff_s", 0)
    monkeypatch.setattr(settings, "send_max_retries", 2)
    calls = {"n": 0}

    class _Boom:
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, *_a, **_k):
            calls["n"] += 1
            raise httpx.ConnectError("sin red")

    monkeypatch.setattr(whatsapp.httpx, "AsyncClient", _Boom)
    assert await whatsapp._post_message("123", {}) is False
    assert calls["n"] == 2


# --------------------------------------------------------------------------- #
#  Notificaciones al negocio (handoff + errores)
# --------------------------------------------------------------------------- #
def _collector():
    sent: list[tuple] = []

    async def send(to, text, pnid):
        sent.append((to, text, pnid))
        return True

    return sent, send


async def test_alerts_notify_business_and_are_idempotent(db_session, seed):
    business = await db_session.get(Business, seed.business_id)
    business.whatsapp_phone_number_id = "111222"
    business.notify_phone = "+34600111222"
    db_session.add_all(
        [
            EventLog(business_id=seed.business_id, type="handoff", payload={}),
            EventLog(business_id=seed.business_id, type="error", payload={}),
        ]
    )
    await db_session.commit()

    sent, send = _collector()
    n = await send_pending_alerts(db_session, send=send)
    assert n == 2
    assert len(sent) == 1
    to, text, pnid = sent[0]
    assert to == "34600111222" and pnid == "111222"
    assert "persona" in text or "esperando" in text

    # Segundo barrido: nada pendiente.
    n2 = await send_pending_alerts(db_session, send=send)
    assert n2 == 0


async def test_alerts_skip_business_without_notify_phone(db_session, seed):
    db_session.add(EventLog(business_id=seed.business_id, type="handoff", payload={}))
    await db_session.commit()
    sent, send = _collector()
    # El negocio sembrado no tiene notify_phone → no se envía, pero se marca.
    n = await send_pending_alerts(db_session, send=send)
    assert sent == []
    assert n == 1  # marcado para no reprocesar en bucle


# --------------------------------------------------------------------------- #
#  Bandeja de handoff: listar y liberar
# --------------------------------------------------------------------------- #
async def test_handoff_inbox_and_release(client, seed, db_session):
    db_session.add(
        Conversation(
            business_id=seed.business_id,
            customer_phone="+34600999000",
            state=ConversationState.HUMAN_HANDOFF,
            context={"window": [{"role": "user", "text": "quiero una persona"}]},
        )
    )
    await db_session.commit()

    inbox = await client.get(f"/admin/businesses/{seed.business_id}/handoffs")
    assert inbox.status_code == 200
    items = inbox.json()
    assert len(items) == 1
    assert items[0]["customer_phone"] == "+34600999000"
    cid = items[0]["id"]

    released = await client.post(
        f"/admin/businesses/{seed.business_id}/conversations/{cid}/release"
    )
    assert released.status_code == 200
    assert released.json()["state"] == "idle"

    # La bandeja queda vacía: el bot retoma la conversación.
    again = await client.get(f"/admin/businesses/{seed.business_id}/handoffs")
    assert again.json() == []
