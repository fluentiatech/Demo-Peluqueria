"""Lista de espera: clientes que quieren un hueco que ahora está lleno.

Cuando una cancelación libera un hueco, un proceso desacoplado (cron) busca aquí
al primero que encaje (mismo servicio, día y profesional compatibles) y le ofrece
el hueco por WhatsApp. Es el relleno automático de cancelaciones.
"""
from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin
from app.models.types import DeterministicString, EncryptedString


class WaitlistStatus(enum.StrEnum):
    WAITING = "waiting"      # esperando a que se libere un hueco
    NOTIFIED = "notified"    # se le ofreció un hueco (pendiente de que confirme)
    FULFILLED = "fulfilled"  # acabó reservando


class WaitlistEntry(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "waitlist"

    business_id: Mapped[str] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    # PII cifrada en reposo (teléfono buscable por igualdad).
    customer_phone: Mapped[str] = mapped_column(DeterministicString(255), nullable=False)
    customer_name: Mapped[str | None] = mapped_column(EncryptedString)

    service_id: Mapped[str] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"), nullable=False
    )
    # Profesional preferido (None = le da igual).
    resource_id: Mapped[str | None] = mapped_column(
        ForeignKey("resources.id", ondelete="SET NULL")
    )
    # Día deseado (None = cualquier día).
    desired_date: Mapped[date | None] = mapped_column(Date)

    status: Mapped[WaitlistStatus] = mapped_column(
        SAEnum(WaitlistStatus, native_enum=False, length=16),
        default=WaitlistStatus.WAITING,
        nullable=False,
    )
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_waitlist_business_status", "business_id", "status"),
        Index("ix_waitlist_match", "business_id", "service_id", "status"),
    )
