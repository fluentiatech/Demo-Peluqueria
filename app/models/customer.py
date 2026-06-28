"""Cliente final que reserva por WhatsApp. Incluye consentimiento RGPD."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin
from app.models.types import DeterministicString, EncryptedString


class Customer(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "customers"

    business_id: Mapped[str] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    # Teléfono en formato E.164 (ej. +34600111222), identificador en WhatsApp.
    # Cifrado determinista: buscable por igualdad y compatible con el UNIQUE.
    phone: Mapped[str] = mapped_column(DeterministicString(255), nullable=False)
    # Nombre cifrado en reposo (solo se muestra; no se busca).
    name: Mapped[str | None] = mapped_column(EncryptedString)

    # RGPD: momento en que el cliente dio su consentimiento.
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Un mismo teléfono es un único cliente dentro de cada negocio.
        UniqueConstraint("business_id", "phone", name="uq_customer_business_phone"),
    )
