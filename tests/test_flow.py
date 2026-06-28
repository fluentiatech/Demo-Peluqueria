"""Tests de la máquina de estados de reserva (Fase 2).

Se scriptea la NLU con un LLM falso: cada turno consume una extracción de la cola.
"""
from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal

from sqlalchemy import select

from app.agent.flow import handle_message
from app.models import Appointment, AppointmentStatus, Business, Customer
from app.tools import book_appointment
from tests.conftest import FakeLLM, next_weekday

PHONE = "34600111222"
NORM = "+34600111222"


async def _business(db_session, seed) -> Business:
    return await db_session.get(Business, seed.business_id)


async def _say(db_session, business, llm, text, mid="wamid.x") -> str:
    return await handle_message(db_session, business, PHONE, text, llm, message_id=mid)


# --------------------------------------------------------------------------- #
#  Reserva completa (camino feliz)
# --------------------------------------------------------------------------- #
async def test_full_booking_flow(db_session, seed):
    business = await _business(db_session, seed)
    monday = next_weekday(0)
    llm = FakeLLM(
        extractions=[
            {"intent": "book", "service": "Corte", "professional": "any"},
            {"intent": "choose", "date": monday.isoformat()},
            {"intent": "choose", "choice_index": 1},
            {"intent": "provide_name", "name": "Marta"},
            {"intent": "confirm"},
        ]
    )

    r1 = await _say(db_session, business, llm, "quiero cita para corte")
    assert "día" in r1.lower()
    r2 = await _say(db_session, business, llm, "el lunes")
    assert "huecos" in r2.lower()
    r3 = await _say(db_session, business, llm, "la primera")
    assert "nombre" in r3.lower()
    r4 = await _say(db_session, business, llm, "Marta")
    assert "confirmo" in r4.lower()
    r5 = await _say(db_session, business, llm, "sí")
    assert "listo" in r5.lower()

    appt = await db_session.scalar(
        select(Appointment).where(Appointment.business_id == seed.business_id)
    )
    assert appt is not None
    # Confirmada por el cliente en el chat → queda CONFIRMED, no PENDING.
    assert appt.status == AppointmentStatus.CONFIRMED
    # Snapshot del servicio congelado en la cita.
    assert appt.service_name == "Corte"
    assert appt.price == Decimal("12.00")
    assert appt.duration_min == 30
    customer = await db_session.get(Customer, appt.customer_id)
    assert customer.phone == NORM
    assert customer.name == "Marta"


async def test_booking_asks_and_uses_professional(db_session, seed):
    """Si no dice profesional, se pregunta; al elegir uno, la cita va a ese recurso."""
    business = await _business(db_session, seed)
    monday = next_weekday(0)
    llm = FakeLLM(
        extractions=[
            {"intent": "book", "service": "Corte"},            # sin profesional
            {"intent": "choose", "professional": "Sillón 2"},  # lo elige
            {"intent": "choose", "date": monday.isoformat()},
            {"intent": "choose", "choice_index": 1},
            {"intent": "provide_name", "name": "Ana"},
            {"intent": "confirm"},
        ]
    )
    r1 = await _say(db_session, business, llm, "quiero corte")
    assert "profesional" in r1.lower()
    r2 = await _say(db_session, business, llm, "con el sillón 2")
    assert "día" in r2.lower()
    await _say(db_session, business, llm, "el lunes")
    await _say(db_session, business, llm, "la primera")
    await _say(db_session, business, llm, "Ana")
    await _say(db_session, business, llm, "sí")

    appt = await db_session.scalar(
        select(Appointment).where(Appointment.business_id == seed.business_id)
    )
    assert appt.resource_id == seed.resource_ids[1]  # Sillón 2


async def test_booking_any_professional_skips_question(db_session, seed):
    business = await _business(db_session, seed)
    llm = FakeLLM(extractions=[{"intent": "book", "service": "Corte"}])
    # "me da igual" lo resuelve el prefilter, sin LLM, y pasa a pedir el día.
    await _say(db_session, business, llm, "quiero corte")
    r = await _say(db_session, business, llm, "me da igual")
    assert "día" in r.lower()


async def test_booking_with_service_and_date_offers_slots_immediately(db_session, seed):
    """Si el cliente da servicio + fecha en un mensaje, se ofertan huecos ya."""
    business = await _business(db_session, seed)
    monday = next_weekday(0)
    llm = FakeLLM(
        extractions=[{"intent": "book", "service": "Corte", "date": monday.isoformat(), "professional": "any"}]
    )
    r1 = await _say(db_session, business, llm, "quiero corte el lunes")
    assert "huecos" in r1.lower()


# --------------------------------------------------------------------------- #
#  Personalización por número de teléfono conocido
# --------------------------------------------------------------------------- #
async def test_greeting_uses_name_for_known_customer(db_session, seed):
    """Si el teléfono ya está guardado, el saludo usa el nombre del cliente."""
    db_session.add(Customer(business_id=seed.business_id, phone=NORM, name="Lucía Pérez"))
    await db_session.commit()
    business = await _business(db_session, seed)
    r = await _say(db_session, business, FakeLLM(), "hola")
    assert "Lucía" in r  # le llama por su (primer) nombre


async def test_greeting_neutral_for_unknown_number(db_session, seed):
    """Número nuevo: saludo sin nombre inventado (el seed no tiene asistente nombrado)."""
    business = await _business(db_session, seed)
    r = await _say(db_session, business, FakeLLM(), "hola")
    assert "¡Hola!" in r  # sin coma + nombre
    assert "Soy " not in r  # el seed no tiene assistant_name configurado


async def test_greeting_introduces_named_assistant(db_session, seed):
    """Si el negocio nombra a su asistente, este se presenta al saludar."""
    business = await _business(db_session, seed)
    business.assistant_name = "Lucía"
    await db_session.commit()
    r = await _say(db_session, business, FakeLLM(), "hola")
    assert "Soy Lucía" in r


async def test_personality_block_in_system_prompt(db_session, seed):
    from app.agent.context import build_system_prompt

    business = await _business(db_session, seed)
    business.assistant_name = "Lucía"
    business.agent_tone = "formal"
    business.use_emojis = False
    await db_session.commit()
    prompt = await build_system_prompt(db_session, business)
    assert "Te llamas Lucía" in prompt
    assert "tratando de usted" in prompt
    assert "No uses emojis" in prompt


async def test_known_customer_booking_skips_name_question(db_session, seed):
    """Cliente conocido: no se le vuelve a pedir el nombre y se le nombra al cerrar."""
    db_session.add(Customer(business_id=seed.business_id, phone=NORM, name="Marta Gil"))
    await db_session.commit()
    business = await _business(db_session, seed)
    monday = next_weekday(0)
    llm = FakeLLM(
        extractions=[
            {"intent": "book", "service": "Corte", "professional": "any"},
            {"intent": "choose", "date": monday.isoformat()},
            {"intent": "choose", "choice_index": 1},
            {"intent": "confirm"},
        ]
    )
    await _say(db_session, business, llm, "quiero corte")
    await _say(db_session, business, llm, "el lunes")
    r3 = await _say(db_session, business, llm, "la primera")
    # No pregunta el nombre: salta directo a confirmar.
    assert "confirmo" in r3.lower()
    assert "nombre" not in r3.lower()
    r4 = await _say(db_session, business, llm, "sí")
    assert "Marta" in r4


async def test_booking_skips_name_when_customer_known(db_session, seed):
    business = await _business(db_session, seed)
    # Cliente ya existente con nombre.
    await book_appointment(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Tinte"],
        start_at=datetime.combine(next_weekday(1), time(9, 0)),
        phone=NORM,
        name="Lola",
    )
    await db_session.commit()

    monday = next_weekday(0)
    llm = FakeLLM(
        extractions=[
            {"intent": "book", "service": "Corte", "professional": "any"},
            {"intent": "choose", "date": monday.isoformat()},
            {"intent": "choose", "choice_index": 1},
            {"intent": "confirm"},
        ]
    )
    await _say(db_session, business, llm, "otra cita")
    await _say(db_session, business, llm, "el lunes")
    r3 = await _say(db_session, business, llm, "la 1")
    # No pide nombre: salta directo a confirmar.
    assert "confirmo" in r3.lower()
    r4 = await _say(db_session, business, llm, "vale")
    assert "listo" in r4.lower()


# --------------------------------------------------------------------------- #
#  Servicio no reconocido → re-pregunta
# --------------------------------------------------------------------------- #
async def test_unknown_service_reprompts(db_session, seed):
    business = await _business(db_session, seed)
    llm = FakeLLM(
        extractions=[
            {"intent": "book", "service": "masaje tailandés"},
            {"intent": "choose", "service": "Corte", "professional": "any"},
        ]
    )
    r1 = await _say(db_session, business, llm, "quiero un masaje")
    assert "servicio" in r1.lower()
    r2 = await _say(db_session, business, llm, "pues un corte")
    assert "día" in r2.lower()


# --------------------------------------------------------------------------- #
#  Cancelación
# --------------------------------------------------------------------------- #
async def test_cancel_flow(db_session, seed):
    business = await _business(db_session, seed)
    appt = await book_appointment(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        start_at=datetime.combine(next_weekday(0), time(10, 0)),
        phone=NORM,
        name="Marta",
    )
    await db_session.commit()

    llm = FakeLLM(extractions=[{"intent": "cancel"}, {"intent": "confirm"}])
    r1 = await _say(db_session, business, llm, "quiero cancelar mi cita")
    assert "cancelo" in r1.lower()
    r2 = await _say(db_session, business, llm, "sí")
    assert "cancelada" in r2.lower()

    assert await db_session.get(Appointment, appt.id) is None  # eliminada


async def test_cancel_without_appointments(db_session, seed):
    business = await _business(db_session, seed)
    llm = FakeLLM(extractions=[{"intent": "cancel"}])
    r = await _say(db_session, business, llm, "cancela mi cita")
    assert "no veo" in r.lower()


# --------------------------------------------------------------------------- #
#  Reprogramación
# --------------------------------------------------------------------------- #
async def test_reschedule_flow(db_session, seed):
    business = await _business(db_session, seed)
    appt = await book_appointment(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        start_at=datetime.combine(next_weekday(0), time(10, 0)),
        phone=NORM,
        name="Marta",
    )
    await db_session.commit()
    original = appt.start_at

    tuesday = next_weekday(1)
    llm = FakeLLM(
        extractions=[
            {"intent": "reschedule"},
            {"intent": "choose", "date": tuesday.isoformat()},
            {"intent": "choose", "choice_index": 1},
            {"intent": "confirm"},
        ]
    )
    await _say(db_session, business, llm, "cambiar mi cita")
    await _say(db_session, business, llm, "el martes")
    await _say(db_session, business, llm, "la primera")
    r = await _say(db_session, business, llm, "sí")
    assert "cambiada" in r.lower()

    refreshed = await db_session.get(Appointment, appt.id)
    assert refreshed.start_at != original


# --------------------------------------------------------------------------- #
#  Handoff y Q&A
# --------------------------------------------------------------------------- #
async def test_handoff_then_silence(db_session, seed):
    business = await _business(db_session, seed)
    llm = FakeLLM(extractions=[{"intent": "handoff"}, {"intent": "question"}])
    r1 = await _say(db_session, business, llm, "quiero hablar con una persona ya!")
    assert "persona" in r1.lower()
    # En handoff el bot calla.
    r2 = await _say(db_session, business, llm, "¿hola?")
    assert r2 == ""


async def test_question_in_idle_uses_qa(db_session, seed):
    business = await _business(db_session, seed)
    llm = FakeLLM(
        reply="Abrimos de 9 a 14.",
        extractions=[{"intent": "question", "question": "¿horario?"}],
    )
    r = await _say(db_session, business, llm, "¿a qué hora abrís?")
    assert r == "Abrimos de 9 a 14."


# --------------------------------------------------------------------------- #
#  Hueco ocupado entre la oferta y la confirmación
# --------------------------------------------------------------------------- #
async def test_slot_taken_between_offer_and_confirm(db_session, seed):
    business = await _business(db_session, seed)
    monday = next_weekday(0)
    # Profesional concreto → la cita queda atada a ese recurso (para forzar choque).
    llm = FakeLLM(
        extractions=[
            {"intent": "book", "service": "Corte", "professional": "Sillón 1"},
            {"intent": "choose", "date": monday.isoformat()},
            {"intent": "choose", "choice_index": 1},
            {"intent": "provide_name", "name": "Marta"},
            {"intent": "confirm"},
        ]
    )
    await _say(db_session, business, llm, "cita corte")
    await _say(db_session, business, llm, "el lunes")
    await _say(db_session, business, llm, "la primera")
    await _say(db_session, business, llm, "Marta")

    # Otra persona ocupa ese mismo hueco/recurso antes de confirmar.
    convo_ctx = await db_session.scalar(
        select(Appointment).where(Appointment.business_id == seed.business_id)
    )
    assert convo_ctx is None  # aún no hay cita
    from app.models import Conversation

    convo = await db_session.scalar(
        select(Conversation).where(Conversation.customer_phone == NORM)
    )
    chosen = convo.context["chosen"]
    await book_appointment(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        start_at=datetime.fromisoformat(chosen["start_at"]),
        phone="+34699998888",
        resource_id=chosen["resource_id"],
    )
    await db_session.commit()

    r = await _say(db_session, business, llm, "sí")
    assert "ocup" in r.lower()
