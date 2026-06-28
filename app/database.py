"""Motor async de SQLAlchemy, factoría de sesiones e inicialización del esquema."""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

# `echo=True` (vía SQL_ECHO) vuelca el SQL generado; útil al depurar consultas.
# El pool y el pre_ping solo aplican a motores reales (Postgres), no a SQLite.
_pool_kwargs: dict[str, object] = (
    {}
    if settings.is_sqlite
    else {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_pre_ping": True,  # descarta conexiones muertas antes de usarlas
        "pool_recycle": 1800,
    }
)

engine = create_async_engine(
    settings.database_url,
    echo=settings.sql_echo,
    future=True,
    # SQLite necesita esto para usarse desde varios hilos/await.
    connect_args={"check_same_thread": False} if settings.is_sqlite else {},
    **_pool_kwargs,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependencia de FastAPI: una sesión por request, con commit/rollback gestionado."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Crea las tablas si no existen.

    Para desarrollo. En producción la fuente de verdad del esquema son las
    migraciones de Alembic (ver carpeta `alembic/`).
    """
    from app.models import Base  # import diferido para registrar los modelos

    async with engine.begin() as conn:
        if settings.is_sqlite:
            # Activa las claves foráneas en SQLite (desactivadas por defecto).
            from sqlalchemy import text

            await conn.execute(text("PRAGMA foreign_keys=ON"))
        await conn.run_sync(Base.metadata.create_all)
