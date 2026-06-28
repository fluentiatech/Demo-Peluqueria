"""Ausencia/bloqueo de un recurso: día libre, vacaciones, baja, formación…

Resta disponibilidad a un recurso concreto durante un intervalo de tiempo.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class TimeOff(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "time_off"

    business_id: Mapped[str] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    resource_id: Mapped[str] = mapped_column(
        ForeignKey("resources.id", ondelete="CASCADE"), nullable=False
    )

    start_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    end_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(String(160))

    __table_args__ = (
        Index("ix_timeoff_resource_range", "resource_id", "start_at", "end_at"),
    )
