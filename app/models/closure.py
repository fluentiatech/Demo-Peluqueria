"""Excepción del horario del negocio para una fecha concreta.

Sobrescribe el horario semanal (`Business.opening_hours`) un día puntual:
  - festivo / vacaciones → `is_closed=True` (sin disponibilidad ese día).
  - apertura especial      → `is_closed=False` + `custom_hours` con los tramos.
"""
from __future__ import annotations

from datetime import date as date_type
from typing import Any

from sqlalchemy import JSON, Date, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class BusinessClosure(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "business_closures"

    business_id: Mapped[str] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date_type] = mapped_column(Date, nullable=False)

    # True = cerrado todo el día. False = abierto con horario especial.
    is_closed: Mapped[bool] = mapped_column(default=True)

    # Tramos del día si es apertura especial: [["09:00","14:00"], ...].
    # Ignorado cuando is_closed=True.
    custom_hours: Mapped[list[Any]] = mapped_column(JSON, default=list)

    reason: Mapped[str | None] = mapped_column(String(160))

    __table_args__ = (
        # Una sola excepción por negocio y fecha.
        UniqueConstraint("business_id", "date", name="uq_closure_business_date"),
    )
