"""Schemas de la lista de espera."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.models.waitlist import WaitlistStatus
from app.schemas.appointment import CustomerIn


class WaitlistCreate(BaseModel):
    service_id: str
    customer: CustomerIn
    resource_id: str | None = None      # profesional preferido (None = cualquiera)
    desired_date: date | None = None    # día deseado (None = cualquier día)


class WaitlistOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    business_id: str
    customer_phone: str
    customer_name: str | None = None
    service_id: str
    resource_id: str | None = None
    desired_date: date | None = None
    status: WaitlistStatus
    created_at: datetime
