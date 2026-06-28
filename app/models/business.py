"""Negocio (tenant). Cada peluquería, clínica o centro es un `Business`."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.resource import Resource
    from app.models.service import Service


class Business(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "businesses"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    # Tipo libre para generalizar: "peluqueria", "estetica", "clinica", "taller"...
    business_type: Mapped[str] = mapped_column(String(60), default="peluqueria")
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Madrid")
    currency: Mapped[str] = mapped_column(String(3), default="EUR")

    phone: Mapped[str | None] = mapped_column(String(32))
    address: Mapped[str | None] = mapped_column(String(255))

    # Número de WhatsApp (phone_number_id de Meta) con el que se enruta el webhook
    # entrante a este negocio. Único cuando está definido.
    whatsapp_phone_number_id: Mapped[str | None] = mapped_column(String(40))

    # Teléfono (E.164) al que avisar al negocio: handoffs y errores del agente.
    notify_phone: Mapped[str | None] = mapped_column(String(32))

    # Bloque de contexto estático que se inyecta (cacheado) en el system prompt:
    # horarios, políticas de cancelación, tono, etc.
    system_context: Mapped[str | None] = mapped_column(Text)

    # --- Personalidad del agente (se inyecta en el prompt y el saludo) ---
    assistant_name: Mapped[str | None] = mapped_column(String(40))  # ej. "Lucía"
    agent_tone: Mapped[str] = mapped_column(String(16), default="cercano")  # cercano|formal
    use_emojis: Mapped[bool] = mapped_column(default=True)
    agent_language: Mapped[str] = mapped_column(String(8), default="auto")  # auto|es|ca|en

    # --- Marca del panel ---
    brand_color: Mapped[str | None] = mapped_column(String(7))   # "#rrggbb"
    logo_url: Mapped[str | None] = mapped_column(String(300))

    # Horario de apertura estructurado (lo usa la tool de disponibilidad).
    # Formato: { "0": [["09:00","14:00"],["16:00","20:00"]], ... } donde la
    # clave es el día de la semana (0=lunes ... 6=domingo). Día ausente = cerrado.
    opening_hours: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # Granularidad de los huecos ofertados, en minutos (ej. 15 o 30).
    slot_granularity_min: Mapped[int] = mapped_column(default=15)

    active: Mapped[bool] = mapped_column(default=True)

    services: Mapped[list[Service]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
    resources: Mapped[list[Resource]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
