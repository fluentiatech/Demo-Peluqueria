"""Schemas del panel de gestión: agenda, clientes y facturación."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.models.appointment import AppointmentStatus
from app.schemas.validators import no_control_chars


# --------------------------------------------------------------------------- #
#  Agenda
# --------------------------------------------------------------------------- #
class AgendaItem(BaseModel):
    id: str
    resource_id: str
    resource_name: str
    service_name: str | None
    price: Decimal
    customer_id: str
    customer_name: str | None
    customer_phone: str
    start_at: datetime
    end_at: datetime
    status: AppointmentStatus


class AgendaResource(BaseModel):
    id: str
    name: str


class AgendaOut(BaseModel):
    date: date
    resources: list[AgendaResource]
    items: list[AgendaItem]


# --------------------------------------------------------------------------- #
#  Clientes
# --------------------------------------------------------------------------- #
class CustomerStat(BaseModel):
    id: str
    name: str | None
    phone: str
    total: int
    completed: int
    no_shows: int
    total_spent: Decimal
    last_visit: datetime | None


class CustomerDetail(CustomerStat):
    appointments: list[AgendaItem]


class CustomerUpdate(BaseModel):
    name: str | None = Field(None, max_length=160)

    @field_validator("name")
    @classmethod
    def _clean_name(cls, v: str | None) -> str | None:
        return no_control_chars(v)


# --------------------------------------------------------------------------- #
#  Facturación
# --------------------------------------------------------------------------- #
class BillingBucket(BaseModel):
    key: str
    revenue: Decimal
    count: int


class StatusCount(BaseModel):
    status: AppointmentStatus
    count: int


class BillingOut(BaseModel):
    date_from: date
    date_to: date
    currency: str = "EUR"
    revenue_billed: Decimal      # citas completadas (asistió)
    revenue_expected: Decimal    # confirmadas/pendientes aún por venir
    revenue_lost: Decimal        # no-shows
    appointments: int
    by_status: list[StatusCount]
    by_service: list[BillingBucket]
    by_professional: list[BillingBucket]
    by_day: list[BillingBucket]
