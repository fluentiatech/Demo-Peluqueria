"""Log de eventos para observabilidad: coste de tokens, errores, transiciones."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class EventLog(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "events_log"

    business_id: Mapped[str | None] = mapped_column(
        ForeignKey("businesses.id", ondelete="SET NULL")
    )
    # ej. "message_in", "message_out", "llm_call", "booking_created", "error"
    type: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # Momento en que se notificó al negocio (para handoff/error). Null = pendiente.
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_events_business_type", "business_id", "type"),
        # Acelera el barrido de eventos pendientes de alertar.
        Index("ix_events_notified", "type", "notified_at"),
    )
