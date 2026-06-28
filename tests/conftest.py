"""Fixtures compartidas: BD SQLite en memoria aislada por test + cliente HTTP.

Cada test recibe una base de datos limpia (engine in-memory con StaticPool para
que todas las sesiones compartan la misma conexión) y, opcionalmente, un negocio
de ejemplo ya sembrado.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

# Los tests SIEMPRE usan SQLite en memoria: aísla de la DATABASE_URL del .env de
# desarrollo (p. ej. Postgres). Debe ir ANTES de importar app.database/app.main,
# que crean el engine a partir de `settings` al importarse.
from app.config import settings as _settings

_settings.database_url = "sqlite+aiosqlite:///:memory:"

from app.agent.context import clear_context_cache  # noqa: E402
from app.agent.llm import Extraction, LLMResult, Message, get_llm_client  # noqa: E402
from app.database import get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, Business, Resource, Service  # noqa: E402
from app.security import clear_rate_limit_state  # noqa: E402
from app.sessions import clear_login_state  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_caches() -> None:
    """Cada test empieza con el limitador y la caché de contexto limpios."""
    clear_rate_limit_state()
    clear_context_cache()
    clear_login_state()

# Horario de prueba: lunes(0)–sábado(5) 09:00–14:00.
OPENING_HOURS = {str(d): [["09:00", "14:00"]] for d in range(6)}


@dataclass
class Seed:
    """Identificadores del negocio sembrado, para usar en los tests."""

    business_id: str
    service_ids: dict[str, str] = field(default_factory=dict)
    resource_ids: list[str] = field(default_factory=list)


def next_weekday(target: int = 0) -> date:
    """Próxima fecha futura cuyo día de la semana sea `target` (0=lunes)."""
    d = date.today() + timedelta(days=1)
    while d.weekday() != target:
        d += timedelta(days=1)
    return d


@pytest_asyncio.fixture
async def engine() -> AsyncIterator:
    eng = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Activa las claves foráneas en cada conexión SQLite.
    @event.listens_for(eng.sync_engine, "connect")
    def _fk_on(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session(session_factory) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def seed(session_factory) -> Seed:
    """Crea un negocio con 2 servicios y 2 recursos, y devuelve sus ids."""
    async with session_factory() as session:
        business = Business(
            name="Salón Test",
            opening_hours=OPENING_HOURS,
            slot_granularity_min=15,
        )
        session.add(business)
        await session.flush()

        corte = Service(
            business_id=business.id, name="Corte", duration_min=30, price=Decimal("12.00")
        )
        tinte = Service(
            business_id=business.id, name="Tinte", duration_min=60, price=Decimal("40.00")
        )
        r1 = Resource(business_id=business.id, name="Sillón 1")
        r2 = Resource(business_id=business.id, name="Sillón 2")
        session.add_all([corte, tinte, r1, r2])
        await session.commit()

        return Seed(
            business_id=business.id,
            service_ids={"Corte": corte.id, "Tinte": tinte.id},
            resource_ids=[r1.id, r2.id],
        )


class FakeLLM:
    """Cliente LLM determinista para tests (no llama a OpenAI).

    `extractions` es una cola de dicts que `extract()` va devolviendo turno a
    turno (la NLU del flujo); agotada, devuelve {"intent": "other"}.
    """

    def __init__(
        self, reply: str = "Respuesta de prueba.", extractions: list[dict] | None = None
    ) -> None:
        self.reply = reply
        self.extractions = list(extractions or [])
        self.calls: list[dict] = []
        self.extract_calls: list[str] = []
        self.extract_models: list[str | None] = []
        self.extract_systems: list[str] = []

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 600,
    ) -> LLMResult:
        self.calls.append({"system": system, "messages": messages, "model": model})
        return LLMResult(
            text=self.reply,
            model=model or "fake",
            prompt_tokens=42,
            completion_tokens=7,
            cached_tokens=0,
        )

    async def extract(
        self, *, system: str, user: str, model: str | None = None
    ) -> Extraction:
        self.extract_calls.append(user)
        self.extract_models.append(model)
        self.extract_systems.append(system)
        data = self.extractions.pop(0) if self.extractions else {"intent": "other"}
        return Extraction(data=data, model=model or "fake")


@pytest_asyncio.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest_asyncio.fixture
async def client(session_factory, fake_llm) -> AsyncIterator[AsyncClient]:
    """Cliente HTTP con la sesión y el LLM apuntando a dobles de test."""

    async def _override() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = _override
    app.dependency_overrides[get_llm_client] = lambda: fake_llm
    # El middleware de auditoría usa AsyncSessionLocal directamente (no inyectable):
    # lo apuntamos al engine de test para que sus escrituras vayan a la BD efímera.
    import app.database as _db

    orig_factory = _db.AsyncSessionLocal
    _db.AsyncSessionLocal = session_factory
    transport = ASGITransport(app=app, client=("127.0.0.1", 12345))
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    _db.AsyncSessionLocal = orig_factory
    app.dependency_overrides.clear()
