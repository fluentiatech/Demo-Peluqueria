"""Registro de mensajes entrantes de WhatsApp, con dedupe atómico.

El `UNIQUE(business_id, message_id)` es la garantía dura contra responder dos
veces a la misma reentrega de Meta: la propia BD rechaza el duplicado, sin
ventana de carrera entre "comprobar" e "insertar".
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class InboundMessage(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "inbound_messages"

    business_id: Mapped[str] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    # message_id de WhatsApp (wamid...).
    message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    from_phone: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "business_id", "message_id", name="uq_inbound_business_message"
        ),
    )
