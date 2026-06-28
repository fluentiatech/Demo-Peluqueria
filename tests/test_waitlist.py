"""Tests de la lista de espera: alta, emparejado, FSM y relleno al liberar hueco."""
from __future__ import annotations

from datetime import datetime, time

from sqlalchemy import select

from app.agent.flow import handle_message
from app.models import (
    Business,
    Conversation,
    ConversationState,
    WaitlistEntry,
    WaitlistStatus,
)
from app.tools import add_to_waitlist, book_appointment, cancel_appointment
from app.tools.waitlist import match_for_freed_slot
from app.waitlist import process_freed_slots
from tests.conftest import FakeLLM, next_weekday

PHONE = "+34600111222"


async def test_add_and_match_for_freed_slot(db_session, seed):
    day = next_weekday(0)
    await add_to_waitlist(
        db_session,
        business_id=seed.business_id,
        phone=PHONE,
        service_id=seed.service_ids["Corte"],
        name="Marta",
        resource_id=None,
        desired_date=day,
    )
    await db_session.commit()

    # Un hueco de ese servicio/día encaja con la entrada (profesional indiferente).
    match = await match_for_freed_slot(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        resource_id=seed.resource_ids[0],
        day=day,
    )
    assert match is not None and match.customer_name == "Marta"

    # Otro día NO encaja.
    other = await match_for_freed_slot(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        resource_id=seed.resource_ids[0],
        day=next_weekday(2),
    )
    assert other is None


async def test_cancel_emits_freed_and_offers_to_waitlist(db_session, seed):
    day = next_weekday(0)
    start = datetime.combine(day, time(10, 0))
    # Una cita ocupa el hueco; otra clienta espera ese servicio/día.
    appt = await book_appointment(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        start_at=start,
        phone="+34600000999",
        resource_id=seed.resource_ids[0],
    )
    await add_to_waitlist(
        db_session,
        business_id=seed.business_id,
        phone=PHONE,
        service_id=seed.service_ids["Corte"],
        name="Marta",
        desired_date=day,
    )
    await db_session.commit()

    # Cancelar libera el hueco (emite el evento slot_freed).
    await cancel_appointment(db_session, seed.business_id, appt.id)
    await db_session.commit()

    sent = []
    async def fake_send(to, text, pnid):
        sent.append((to, text))
        return True

    offered = await process_freed_slots(db_session, send=fake_send)
    assert offered == 1
    # En desarrollo (sin token) no se envía, pero la entrada queda marcada.
    entry = await db_session.scalar(select(WaitlistEntry))
    assert entry.status == WaitlistStatus.NOTIFIED

    # La conversación de la clienta quedó lista para que un "sí" reserve.
    convo = await db_session.scalar(
        select(Conversation).where(Conversation.customer_phone == PHONE)
    )
    assert convo is not None
    assert convo.state == ConversationState.CONFIRMING
    assert convo.context["action"] == "book"


async def test_offered_slot_books_on_yes(db_session, seed):
    """Tras ofrecer el hueco, un 'sí' de la clienta lo reserva (flujo normal)."""
    day = next_weekday(0)
    start = datetime.combine(day, time(10, 0))
    appt = await book_appointment(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        start_at=start,
        phone="+34600000999",
        resource_id=seed.resource_ids[0],
    )
    await add_to_waitlist(
        db_session, business_id=seed.business_id, phone=PHONE,
        service_id=seed.service_ids["Corte"], name="Marta", desired_date=day,
    )
    await db_session.commit()
    await cancel_appointment(db_session, seed.business_id, appt.id)
    await db_session.commit()
    await process_freed_slots(db_session, send=None)  # sin token: no envía

    business = await db_session.get(Business, seed.business_id)
    reply = await handle_message(db_session, business, PHONE, "sí", FakeLLM())
    assert "listo" in reply.lower()


async def test_flow_offers_waitlist_when_no_slots(db_session, seed):
    """Si no hay huecos, el agente ofrece la lista de espera y un 'sí' apunta."""
    business = await db_session.get(Business, seed.business_id)
    # Cerramos toda la semana → no hay ningún hueco (ni en el rango de respaldo).
    business.opening_hours = {}
    await db_session.commit()
    day = next_weekday(0)
    llm = FakeLLM(extractions=[
        {"intent": "book", "service": "Corte", "professional": "any"},
        {"intent": "choose", "date": day.isoformat()},
    ])
    r1 = await handle_message(db_session, business, PHONE, "quiero corte", llm)
    assert "día" in r1.lower()
    r2 = await handle_message(db_session, business, PHONE, "el lunes", llm)
    assert "lista de espera" in r2.lower() or "avise" in r2.lower()

    r3 = await handle_message(db_session, business, PHONE, "sí", llm)
    assert "aviso" in r3.lower()
    entry = await db_session.scalar(select(WaitlistEntry))
    assert entry is not None and entry.service_id == seed.service_ids["Corte"]
