"""Schemas para disponibilidad y reservas."""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import settings
from app.models.appointment import AppointmentStatus
from app.schemas.validators import no_control_chars


class Slot(BaseModel):
    """Hueco disponible para un servicio sobre un recurso concreto."""

    resource_id: str
    resource_name: str
    start_at: datetime
    end_at: datetime


class AvailabilityRequest(BaseModel):
    service_id: str
    date_from: date
    date_to: date

    @model_validator(mode="after")
    def _check_range(self) -> AvailabilityRequest:
        if self.date_to < self.date_from:
            raise ValueError("date_to no puede ser anterior a date_from")
        if (self.date_to - self.date_from).days > settings.availability_max_days:
            raise ValueError(
                f"El rango no puede superar {settings.availability_max_days} días"
            )
        return self


_E164 = re.compile(r"^\+?[1-9]\d{6,14}$")


class CustomerIn(BaseModel):
    phone: str = Field(..., min_length=5, max_length=32)
    name: str | None = Field(None, max_length=160)

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, v: str) -> str:
        cleaned = v.strip().replace(" ", "")
        if not _E164.match(cleaned):
            raise ValueError("Teléfono no válido (formato E.164, p. ej. +34600111222)")
        return cleaned

    @field_validator("name")
    @classmethod
    def _clean_name(cls, v: str | None) -> str | None:
        return no_control_chars(v)


class BookingRequest(BaseModel):
    service_id: str
    start_at: datetime
    customer: CustomerIn
    resource_id: str | None = Field(
        None, description="Si se omite, se asigna el primer recurso libre"
    )
    idempotency_key: str | None = Field(
        None, description="message_id de WhatsApp para evitar reservas duplicadas"
    )
    notes: str | None = Field(None, max_length=1000)
    force: bool = Field(
        False, description="Alta manual: salta horario/cierre (no el anti-doble-reserva)"
    )

    @field_validator("notes")
    @classmethod
    def _clean_notes(cls, v: str | None) -> str | None:
        return no_control_chars(v)


class AppointmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    business_id: str
    service_id: str
    resource_id: str
    customer_id: str
    start_at: datetime
    end_at: datetime
    status: AppointmentStatus
    # Snapshot del servicio al reservar (histórico fiable).
    service_name: str | None = None
    price: Decimal | None = None
    duration_min: int | None = None
    notes: str | None = None


class RescheduleRequest(BaseModel):
    new_start_at: datetime
    new_resource_id: str | None = Field(
        None, description="Mover a otro profesional (si se omite, mantiene el actual)"
    )


class StatusUpdate(BaseModel):
    """Cambio manual de estado desde el back-office."""

    status: AppointmentStatus


class PriceInfo(BaseModel):
    service_id: str
    name: str
    duration_min: int
    price: Decimal
