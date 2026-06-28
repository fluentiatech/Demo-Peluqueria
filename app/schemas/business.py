"""Schemas de negocio y recurso."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.validators import validate_weekly_hours

_HEX_COLOR = r"^#[0-9a-fA-F]{6}$"

# Horario semanal: clave = día (0=lunes..6=domingo), valor = lista de tramos
# [["09:00","14:00"], ["16:00","20:00"]].
WeeklyHours = dict[str, list[list[str]]]


class BusinessBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    business_type: str = Field("peluqueria", max_length=60)
    timezone: str = Field("Europe/Madrid", max_length=64)
    currency: str = Field("EUR", min_length=3, max_length=3)
    phone: str | None = Field(None, max_length=32)
    address: str | None = Field(None, max_length=255)
    system_context: str | None = Field(None, max_length=8000)
    whatsapp_phone_number_id: str | None = Field(None, max_length=40)
    notify_phone: str | None = Field(None, max_length=32)
    opening_hours: WeeklyHours = Field(default_factory=dict)
    slot_granularity_min: int = Field(15, gt=0, le=240)

    # Personalidad del agente.
    assistant_name: str | None = Field(None, max_length=40)
    agent_tone: Literal["cercano", "formal"] = "cercano"
    use_emojis: bool = True
    agent_language: Literal["auto", "es", "ca", "en"] = "auto"
    # Marca del panel.
    brand_color: str | None = Field(None, pattern=_HEX_COLOR)
    logo_url: str | None = Field(None, max_length=300)

    @field_validator("opening_hours")
    @classmethod
    def _check_hours(cls, v: dict) -> dict:
        return validate_weekly_hours(v)


class BusinessCreate(BusinessBase):
    pass


class BusinessUpdate(BaseModel):
    """Edición parcial del negocio (horario, granularidad, datos de contacto)."""

    name: str | None = Field(None, min_length=1, max_length=160)
    business_type: str | None = Field(None, max_length=60)
    timezone: str | None = Field(None, max_length=64)
    phone: str | None = Field(None, max_length=32)
    address: str | None = Field(None, max_length=255)
    system_context: str | None = Field(None, max_length=8000)
    whatsapp_phone_number_id: str | None = Field(None, max_length=40)
    notify_phone: str | None = Field(None, max_length=32)
    opening_hours: WeeklyHours | None = None
    slot_granularity_min: int | None = Field(None, gt=0, le=240)
    # Personalidad del agente y marca del panel.
    assistant_name: str | None = Field(None, max_length=40)
    agent_tone: Literal["cercano", "formal"] | None = None
    use_emojis: bool | None = None
    agent_language: Literal["auto", "es", "ca", "en"] | None = None
    brand_color: str | None = Field(None, pattern=_HEX_COLOR)
    logo_url: str | None = Field(None, max_length=300)

    @field_validator("opening_hours")
    @classmethod
    def _check_hours(cls, v: dict | None) -> dict | None:
        return validate_weekly_hours(v) if v is not None else v


class BusinessOut(BusinessBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    active: bool


class ResourceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    active: bool = True
    # Horario propio (mismo formato). Vacío = hereda el del negocio.
    working_hours: WeeklyHours = Field(default_factory=dict)

    @field_validator("working_hours")
    @classmethod
    def _check_hours(cls, v: dict) -> dict:
        return validate_weekly_hours(v)


class ResourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    business_id: str
    name: str
    active: bool
    working_hours: dict[str, Any] = Field(default_factory=dict)
