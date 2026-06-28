"""Schemas de entrada/salida para servicios."""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ServiceBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    description: str | None = None
    duration_min: int = Field(..., gt=0, le=24 * 60, description="Duración en minutos")
    price: Decimal = Field(..., ge=0, description="Precio del servicio")
    category: str | None = Field(None, max_length=80)
    buffer_before_min: int = Field(0, ge=0, le=240, description="Preparación (min)")
    buffer_after_min: int = Field(0, ge=0, le=240, description="Limpieza (min)")
    active: bool = True


class ServiceCreate(ServiceBase):
    pass


class ServiceUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=160)
    description: str | None = None
    duration_min: int | None = Field(None, gt=0, le=24 * 60)
    price: Decimal | None = Field(None, ge=0)
    category: str | None = Field(None, max_length=80)
    buffer_before_min: int | None = Field(None, ge=0, le=240)
    buffer_after_min: int | None = Field(None, ge=0, le=240)
    active: bool | None = None


class ServiceResourcesIn(BaseModel):
    """Asignación de recursos cualificados a un servicio (vacío = cualquiera)."""

    resource_ids: list[str] = Field(default_factory=list)


class ServiceOut(ServiceBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    business_id: str
