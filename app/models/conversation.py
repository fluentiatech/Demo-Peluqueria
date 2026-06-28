"""Estado de la conversación de WhatsApp (máquina de estados de la reserva).

Persistido en BD como respaldo; Redis es la caché caliente en producción.
"""
from __future__ import annotations

import enum
from typing import Any

from sqlalchemy import JSON, ForeignKey, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin
from app.models.types import DeterministicString


class ConversationState(enum.StrEnum):
    IDLE = "idle"
    COLLECTING_SERVICE = "collecting_service"
    COLLECTING_PROFESSIONAL = "collecting_professional"
    COLLECTING_DATETIME = "collecting_datetime"
    COLLECTING_CONTACT = "collecting_contact"
    CONFIRMING = "confirming"
    WAITLIST_OFFER = "waitlist_offer"  # sin huecos: ¿te apunto a la lista de espera?
    MANAGE_BOOKING = "manage_booking"
    HUMAN_HANDOFF = "human_handoff"


class Conversation(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "conversations"

    business_id: Mapped[str] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    # Cifrado determinista: la conversación se enruta por este teléfono (igualdad).
    customer_phone: Mapped[str] = mapped_column(
        DeterministicString(255), nullable=False
    )

    state: Mapped[ConversationState] = mapped_column(
        SAEnum(ConversationState, native_enum=False, length=30),
        default=ConversationState.IDLE,
        nullable=False,
    )

    # Datos parciales recogidos durante el flujo (servicio elegido, fecha tentativa,
    # ventana deslizante de mensajes, etc.).
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    __table_args__ = (
        UniqueConstraint(
            "business_id", "customer_phone", name="uq_conversation_business_phone"
        ),
    )
