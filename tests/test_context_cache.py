"""Tests de la caché de contexto y del abaratamiento de la NLU."""
from __future__ import annotations

from datetime import date

from app.agent import context
from app.agent.nlu import classify
from app.config import settings
from app.models import Business
from tests.conftest import FakeLLM


async def test_context_is_cached_and_invalidated(db_session, seed, monkeypatch):
    business = await db_session.get(Business, seed.business_id)
    calls = {"n": 0}
    orig = context.build_system_prompt

    async def _counting(session, biz):
        calls["n"] += 1
        return await orig(session, biz)

    monkeypatch.setattr(context, "build_system_prompt", _counting)
    context.clear_context_cache()

    c1 = await context.get_business_context(db_session, business)
    c2 = await context.get_business_context(db_session, business)
    assert calls["n"] == 1  # la segunda vez sale de caché (no reconstruye)
    assert c1 is c2
    assert {s.name for s in c1.services} == {"Corte", "Tinte"}

    # Al invalidar (p. ej. cambió el catálogo), se reconstruye.
    context.invalidate_context(business.id)
    await context.get_business_context(db_session, business)
    assert calls["n"] == 2


async def test_context_cache_expires(db_session, seed, monkeypatch):
    business = await db_session.get(Business, seed.business_id)
    calls = {"n": 0}
    orig = context.build_system_prompt

    async def _counting(session, biz):
        calls["n"] += 1
        return await orig(session, biz)

    monkeypatch.setattr(context, "build_system_prompt", _counting)
    monkeypatch.setattr(settings, "context_cache_ttl_s", 0)  # caduca al instante
    context.clear_context_cache()

    await context.get_business_context(db_session, business)
    await context.get_business_context(db_session, business)
    assert calls["n"] == 2  # con TTL 0 no hay reutilización


async def test_nlu_uses_configured_cheapest_model(monkeypatch):
    monkeypatch.setattr(settings, "openai_model_nlu", "modelo-barato")
    llm = FakeLLM(extractions=[{"intent": "book", "service": "Corte"}])
    await classify(
        llm, services=["Corte", "Tinte"], state="idle", text="cita", today=date.today()
    )
    assert llm.extract_models[-1] == "modelo-barato"


async def test_nlu_catalog_goes_in_cacheable_system_prefix(monkeypatch):
    monkeypatch.setattr(settings, "openai_model_nlu", "")
    llm = FakeLLM(extractions=[{"intent": "book"}])
    await classify(
        llm, services=["Corte", "Tinte"], state="idle", text="hola", today=date.today()
    )
    system = llm.extract_systems[-1]
    user = llm.extract_calls[-1]
    # El catálogo (estable por negocio) va en el system → prefijo cacheable.
    assert "Corte" in system and "Tinte" in system
    # En el user solo lo variable del turno; nada de catálogo.
    assert "Corte" not in user
