"""Tests de la optimización de coste (Fase 3): prefilter, routing y ventana."""
from __future__ import annotations

from datetime import datetime, time

from sqlalchemy import func, select

from app.agent.flow import handle_message
from app.agent.prefilter import fast_classify
from app.agent.routing import choose_model
from app.config import settings
from app.models import Business, ConversationState, EventLog
from app.tools import book_appointment
from tests.conftest import FakeLLM, next_weekday

PHONE = "+34600111222"
S = ConversationState


# --------------------------------------------------------------------------- #
#  Prefilter (unidad)
# --------------------------------------------------------------------------- #
def test_prefilter_confirm_and_deny():
    assert fast_classify(S.CONFIRMING, "sí", False).data["intent"] == "confirm"
    assert fast_classify(S.CONFIRMING, "Vale!", False).data["intent"] == "confirm"
    assert fast_classify(S.CONFIRMING, "no", False).data["intent"] == "deny"
    assert fast_classify(S.CONFIRMING, "👍", False).data["intent"] == "confirm"


def test_prefilter_greeting_only_in_idle():
    assert fast_classify(S.IDLE, "hola", False).data["intent"] == "greeting"
    assert fast_classify(S.IDLE, "gracias", False).data["intent"] == "greeting"
    # En CONFIRMING un saludo no es trivial → al LLM.
    assert fast_classify(S.CONFIRMING, "hola", False) is None


def test_prefilter_numeric_choice_needs_offered():
    assert fast_classify(S.COLLECTING_DATETIME, "la 2", True).data["choice_index"] == 2
    assert fast_classify(S.COLLECTING_DATETIME, "3", True).data["choice_index"] == 3
    # Sin huecos ofertados, un número es ambiguo → al LLM.
    assert fast_classify(S.COLLECTING_DATETIME, "2", False) is None


def test_prefilter_unknown_returns_none():
    assert fast_classify(S.IDLE, "quiero un corte mañana", False) is None


# --------------------------------------------------------------------------- #
#  Routing de modelo
# --------------------------------------------------------------------------- #
def test_routing_simple_uses_fast():
    assert choose_model("¿a qué hora abrís?") == settings.openai_model_fast


def test_routing_complex_escalates():
    assert choose_model("¿por qué el tinte es mejor que las mechas?") == settings.openai_model_smart
    assert choose_model("x" * 200) == settings.openai_model_smart


# --------------------------------------------------------------------------- #
#  Integración: el prefilter evita la llamada al LLM
# --------------------------------------------------------------------------- #
async def test_greeting_skips_llm(db_session, seed):
    business = await db_session.get(Business, seed.business_id)
    llm = FakeLLM()
    reply = await handle_message(db_session, business, PHONE, "hola", llm)
    assert "hola" in reply.lower()
    # No se llamó al LLM (ni extract ni complete) y se registró el corte.
    assert llm.extract_calls == []
    assert llm.calls == []
    prefiltered = await db_session.scalar(
        select(func.count()).select_from(EventLog).where(EventLog.type == "prefilter")
    )
    assert prefiltered == 1


async def test_confirm_skips_llm_and_books(db_session, seed):
    """Tras presentar la confirmación, un 'sí' cierra la cita sin llamar al LLM."""
    business = await db_session.get(Business, seed.business_id)
    monday = next_weekday(0)
    llm = FakeLLM(
        extractions=[
            {"intent": "book", "service": "Corte", "date": monday.isoformat(), "professional": "any"},
            {"intent": "choose", "choice_index": 1},
            {"intent": "provide_name", "name": "Marta"},
        ]
    )
    await handle_message(db_session, business, PHONE, "corte el lunes", llm)
    await handle_message(db_session, business, PHONE, "la primera", llm)
    await handle_message(db_session, business, PHONE, "Marta", llm)
    extract_before = len(llm.extract_calls)

    reply = await handle_message(db_session, business, PHONE, "sí", llm)
    assert "listo" in reply.lower()
    # El "sí" no añadió ninguna llamada de extracción.
    assert len(llm.extract_calls) == extract_before


# --------------------------------------------------------------------------- #
#  Ventana de conversación acotada
# --------------------------------------------------------------------------- #
async def test_window_is_bounded_and_used_in_handoff(db_session, seed):
    from app.agent.flow import WINDOW_TURNS
    from app.models import Conversation

    business = await db_session.get(Business, seed.business_id)
    llm = FakeLLM(extractions=[{"intent": "question"}] * 10)
    for i in range(10):
        await handle_message(db_session, business, PHONE, f"pregunta {i}", llm)

    convo = await db_session.scalar(
        select(Conversation).where(Conversation.customer_phone == PHONE)
    )
    assert len(convo.context["window"]) <= WINDOW_TURNS

    # El handoff adjunta la ventana reciente para el humano.
    llm2 = FakeLLM(extractions=[{"intent": "handoff"}])
    await handle_message(db_session, business, PHONE, "quiero una persona", llm2)
    event = await db_session.scalar(
        select(EventLog).where(EventLog.type == "handoff")
    )
    assert event.payload.get("window")


async def test_cancel_book_unaffected_by_window(db_session, seed):
    """La ventana no rompe el flujo: una cancelación sigue funcionando."""
    business = await db_session.get(Business, seed.business_id)
    await book_appointment(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        start_at=datetime.combine(next_weekday(0), time(10, 0)),
        phone=PHONE,
        name="Marta",
    )
    await db_session.commit()
    llm = FakeLLM(extractions=[{"intent": "cancel"}])
    r1 = await handle_message(db_session, business, PHONE, "cancelar mi cita", llm)
    assert "cancelo" in r1.lower()
    r2 = await handle_message(db_session, business, PHONE, "sí", llm)  # prefilter
    assert "cancelada" in r2.lower()
