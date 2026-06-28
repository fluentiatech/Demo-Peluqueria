"""Recurso reservable: silla, box, sillón o profesional.

Es la unidad sobre la que se aplica el constraint anti-doble-reserva:
un recurso no puede tener dos citas solapadas.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.associations import service_resource
from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.business import Business
    from app.models.service import Service


class Resource(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "resources"

    business_id: Mapped[str] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    active: Mapped[bool] = mapped_column(default=True)

    # Horario propio del recurso (mismo formato que Business.opening_hours).
    # Vacío = hereda el horario del negocio. Si está definido, la disponibilidad
    # usa la intersección (negocio ∩ recurso).
    working_hours: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    business: Mapped[Business] = relationship(back_populates="resources")

    # Servicios que este recurso puede realizar (ver associations).
    services: Mapped[list[Service]] = relationship(
        secondary=service_resource, back_populates="qualified_resources"
    )

    __table_args__ = (Index("ix_resources_business", "business_id"),)
