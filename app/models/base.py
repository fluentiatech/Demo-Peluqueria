"""Declarative Base, tipos portables y mixins comunes a todos los modelos."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Base declarativa compartida por todos los modelos."""


class UUIDMixin:
    """Clave primaria UUID (string portable entre SQLite y Postgres)."""

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid
    )


class TimestampMixin:
    """Marcas de creación y actualización."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
    )
