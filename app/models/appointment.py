"""Cita: el corazón transaccional. Aquí viven los constraints anti-doble-reserva."""
from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin
from app.models.types import EncryptedString


class AppointmentStatus(enum.StrEnum):
    PENDING = "pending"        # creada, a la espera de confirmación
    CONFIRMED = "confirmed"    # confirmada por el cliente
    COMPLETED = "completed"    # asistió
    NO_SHOW = "no_show"        # no vino
    # Cancelar una cita la ELIMINA (libera el hueco), no deja estado "cancelada".


class Appointment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "appointments"

    business_id: Mapped[str] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[str] = mapped_column(
        ForeignKey("services.id", ondelete="RESTRICT"), nullable=False
    )
    resource_id: Mapped[str] = mapped_column(
        ForeignKey("resources.id", ondelete="RESTRICT"), nullable=False
    )
    customer_id: Mapped[str] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False
    )

    # Horario del servicio en sí (lo que ve el cliente).
    start_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    end_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Ventana real que ocupa el recurso = servicio + buffers de preparación/limpieza.
    # La detección de solapes usa estos campos, no start_at/end_at.
    block_start_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    block_end_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    status: Mapped[AppointmentStatus] = mapped_column(
        SAEnum(AppointmentStatus, native_enum=False, length=20),
        default=AppointmentStatus.PENDING,
        nullable=False,
    )

    # Snapshot del servicio EN EL MOMENTO de reservar (histórico fiable): aunque
    # luego cambie el precio o el nombre del servicio, la cita conserva los suyos.
    service_name: Mapped[str | None] = mapped_column(String(160))
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    duration_min: Mapped[int] = mapped_column(nullable=False, default=0)

    # Idempotencia: el message_id de WhatsApp evita crear la cita dos veces
    # cuando Meta reentrega el webhook.
    idempotency_key: Mapped[str | None] = mapped_column(String(128))

    notes: Mapped[str | None] = mapped_column(EncryptedString)

    # Momento en que se envió el recordatorio (null = pendiente de enviar).
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Anti-doble-reserva: un recurso no puede tener dos citas a la misma hora.
        # (Para solapamientos parciales en Postgres se usaría un EXCLUDE con
        #  tsrange + btree_gist; ver nota en tools/booking.py.)
        UniqueConstraint("resource_id", "start_at", name="uq_resource_slot"),
        # Idempotencia por negocio.
        UniqueConstraint(
            "business_id", "idempotency_key", name="uq_business_idempotency"
        ),
        Index("ix_appointments_business_start", "business_id", "start_at"),
        Index("ix_appointments_customer", "customer_id"),
        # Acelera el barrido de recordatorios pendientes.
        Index("ix_appointments_reminder", "reminder_sent_at", "start_at"),
    )
