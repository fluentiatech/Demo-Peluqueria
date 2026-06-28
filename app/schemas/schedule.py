"""Schemas de excepciones de calendario: cierres del negocio y ausencias de recursos."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.validators import validate_intervals


class ClosureCreate(BaseModel):
    date: date
    is_closed: bool = True
    # Tramos si es apertura especial: [["09:00","14:00"], ...]. Ignorado si is_closed.
    custom_hours: list[list[str]] = Field(default_factory=list)
    reason: str | None = Field(None, max_length=160)

    @field_validator("custom_hours")
    @classmethod
    def _check_hours(cls, v: list) -> list:
        return validate_intervals(v)


class ClosureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    business_id: str
    date: date
    is_closed: bool
    custom_hours: list = Field(default_factory=list)
    reason: str | None = None


class DayInfo(BaseModel):
    """Estado efectivo de un día concreto (lo mismo que ve la reserva por WhatsApp)."""

    date: date
    weekday: int  # 0=lunes .. 6=domingo
    is_open: bool
    kind: str  # "cerrado" | "continuo" | "partido"
    intervals: list[list[str]] = Field(default_factory=list)  # [["09:00","14:00"], ...]
    is_special: bool = False  # hay un cierre/apertura especial para esta fecha
    reason: str | None = None


class TimeOffCreate(BaseModel):
    resource_id: str
    start_at: datetime
    end_at: datetime
    reason: str | None = Field(None, max_length=160)

    @model_validator(mode="after")
    def _check_range(self) -> TimeOffCreate:
        if self.end_at <= self.start_at:
            raise ValueError("end_at debe ser posterior a start_at")
        return self


class TimeOffOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    business_id: str
    resource_id: str
    start_at: datetime
    end_at: datetime
    reason: str | None = None
