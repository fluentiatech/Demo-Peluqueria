"""Tests del orquestador del webhook entrante de WhatsApp."""
from __future__ import annotations

from sqlalchemy import func, select

from app.agent.handler import process_incoming
from app.integrations.whatsapp import parse_incoming
from app.models import Business, EventLog, InboundMessage
from tests.conftest import FakeLLM


def _payload(text, from_="34600111222", message_id="wamid.1", phone_number_id="PN"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": phone_number_id},
                            "contacts": [{"wa_id": from_, "profile": {"name": "Ana"}}],
                            "messages": [
                                {
                                    "from": from_,
                                    "id": message_id,
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ]
            }
        ],
    }


def _collector():
    sent: list[tuple[str, str, str]] = []

    async def send(to: str, text: str, pnid: str) -> bool:
        sent.append((to, text, pnid))
        return True

    return sent, send


def test_parse_ignores_status_only_payload():
    status_payload = {
        "entry": [{"changes": [{"value": {"statuses": [{"status": "delivered"}]}}]}]
    }
    assert parse_incoming(status_payload) == []


async def test_process_answers_and_sends(db_session, seed):
    sent, send = _collector()
    llm = FakeLLM("Abrimos de 9 a 14.")

    n = await process_incoming(db_session, _payload("¿horario?"), llm, send)

    assert n == 1
    assert len(sent) == 1
    assert sent[0][1] == "Abrimos de 9 a 14."
    # El mensaje entrante quedó registrado (dedupe) y se logueó el coste del LLM.
    inbound = await db_session.scalar(
        select(func.count())
        .select_from(InboundMessage)
        .where(InboundMessage.business_id == seed.business_id)
    )
    assert inbound == 1
    types = (
        await db_session.scalars(
            select(EventLog.type).where(EventLog.business_id == seed.business_id)
        )
    ).all()
    assert "llm_call" in types


async def test_idempotent_redelivery_is_not_answered_twice(db_session, seed):
    sent, send = _collector()
    llm = FakeLLM()
    payload = _payload("hola", message_id="wamid.SAME")

    first = await process_incoming(db_session, payload, llm, send)
    second = await process_incoming(db_session, payload, llm, send)

    assert first == 1
    assert second == 0
    assert len(sent) == 1
    # Solo se registró una vez (el UNIQUE bloqueó la reentrega).
    count = await db_session.scalar(
        select(func.count())
        .select_from(InboundMessage)
        .where(InboundMessage.message_id == "wamid.SAME")
    )
    assert count == 1


async def test_two_messages_same_payload_dedupe_within_batch(db_session, seed):
    """Dos mensajes con el mismo id en un mismo lote: solo uno se responde."""
    sent, send = _collector()
    payload = _payload("hola")
    # Duplica el mensaje dentro del payload.
    msgs = payload["entry"][0]["changes"][0]["value"]["messages"]
    msgs.append(dict(msgs[0]))

    n = await process_incoming(db_session, payload, FakeLLM(), send)
    assert n == 1
    assert len(sent) == 1


async def test_messages_per_payload_cap(db_session, seed, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "max_messages_per_payload", 1)
    sent, send = _collector()
    payload = _payload("hola")
    msgs = payload["entry"][0]["changes"][0]["value"]["messages"]
    msgs.append({**msgs[0], "id": "wamid.2"})  # segundo mensaje distinto

    n = await process_incoming(db_session, payload, FakeLLM(), send)
    assert n == 1  # solo se procesa el primero
    assert len(sent) == 1


async def test_outbound_reply_is_truncated(db_session, seed, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "max_outbound_chars", 5)
    sent, send = _collector()
    llm = FakeLLM(reply="abcdefghij")  # 10 caracteres
    await process_incoming(db_session, _payload("¿horario?"), llm, send)
    assert sent and len(sent[0][1]) == 5


async def test_routing_by_phone_number_id(db_session, seed):
    # El negocio sembrado escucha en el número "PN-A".
    business = await db_session.get(Business, seed.business_id)
    business.whatsapp_phone_number_id = "PN-A"
    # Un segundo negocio rompe el fallback single-tenant.
    db_session.add(Business(name="Otro", whatsapp_phone_number_id="PN-B"))
    await db_session.commit()

    sent, send = _collector()
    llm = FakeLLM()

    matched = await process_incoming(
        db_session, _payload("hola", phone_number_id="PN-A"), llm, send
    )
    unmatched = await process_incoming(
        db_session,
        _payload("hola", message_id="wamid.2", phone_number_id="PN-DESCONOCIDO"),
        llm,
        send,
    )

    assert matched == 1
    assert unmatched == 0
    assert len(sent) == 1


async def test_failed_generation_logs_error_and_keeps_going(db_session, seed):
    sent, send = _collector()

    class BoomLLM(FakeLLM):
        async def complete(self, **kwargs):
            raise RuntimeError("modelo caído")

    # Mensaje no trivial → no lo atrapa el prefilter, pasa al LLM (que falla).
    n = await process_incoming(db_session, _payload("una consulta cualquiera"), BoomLLM(), send)
    assert n == 0
    assert sent == []
    errors = await db_session.scalar(
        select(func.count()).select_from(EventLog).where(EventLog.type == "error")
    )
    assert errors == 1
