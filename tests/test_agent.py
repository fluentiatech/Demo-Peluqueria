"""Tests del agente de Q&A (Fase 1) con un LLM falso."""
from __future__ import annotations

from sqlalchemy import func, select

from app.agent.context import build_system_prompt
from app.agent.qa import answer_question
from app.models import Business, EventLog
from tests.conftest import FakeLLM


async def test_system_prompt_includes_catalog_and_hours(db_session, seed):
    business = await db_session.get(Business, seed.business_id)
    prompt = await build_system_prompt(db_session, business)

    assert "Corte" in prompt and "Tinte" in prompt
    assert "12.00" in prompt  # precio del corte
    assert "30 min" in prompt  # duración
    assert "Lunes" in prompt   # horario formateado


async def test_answer_question_returns_text_and_logs_cost(db_session, seed):
    business = await db_session.get(Business, seed.business_id)
    llm = FakeLLM("¡Claro! Un corte cuesta 12 €.")

    answer = await answer_question(db_session, business, "¿cuánto vale un corte?", llm)
    assert answer == "¡Claro! Un corte cuesta 12 €."

    # El system prompt llega como contexto cacheable y el mensaje del usuario también.
    assert llm.calls[0]["messages"][-1]["content"] == "¿cuánto vale un corte?"

    # Se registró el coste de la llamada.
    count = await db_session.scalar(
        select(func.count())
        .select_from(EventLog)
        .where(EventLog.type == "llm_call")
    )
    assert count == 1


async def test_ask_endpoint(client, seed):
    resp = await client.post(
        f"/admin/businesses/{seed.business_id}/ask",
        json={"message": "¿a qué hora abrís?"},
    )
    assert resp.status_code == 200
    assert resp.json()["answer"] == "Respuesta de prueba."


async def test_ask_endpoint_unknown_business_404(client):
    # UUID válido pero inexistente → 404.
    resp = await client.post(
        "/admin/businesses/00000000-0000-4000-8000-000000000000/ask",
        json={"message": "hola"},
    )
    assert resp.status_code == 404
