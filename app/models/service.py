"""Servicio ofertado por un negocio: nombre, DURACIÓN y PRECIO.

Es el catálogo que el agente consulta para informar y para calcular el hueco
de tiempo que ocupa una cita.
"""
from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.associations import service_resource
from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.business import Business
    from app.models.resource import Resource


class Service(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "services"

    business_id: Mapped[str] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    # Duración de la cita en minutos. Determina cuánto recurso se bloquea.
    duration_min: Mapped[int] = mapped_column(nullable=False)

    # Precio del servicio. Numeric para evitar errores de coma flotante con dinero.
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0)

    # Categoría opcional para agrupar (ej. "corte", "color", "tratamiento").
    category: Mapped[str | None] = mapped_column(String(80))

    # Tiempo de preparación/limpieza que bloquea el recurso antes y después del
    # servicio (no se cobra ni se muestra como cita, pero espacia las reservas).
    buffer_before_min: Mapped[int] = mapped_column(default=0)
    buffer_after_min: Mapped[int] = mapped_column(default=0)

    active: Mapped[bool] = mapped_column(default=True)

    business: Mapped[Business] = relationship(back_populates="services")

    # Recursos cualificados para este servicio (vacío = cualquiera; ver associations).
    qualified_resources: Mapped[list[Resource]] = relationship(
        secondary=service_resource, back_populates="services"
    )

    __table_args__ = (
        # Acelera el listado del catálogo por negocio (consulta más frecuente).
        Index("ix_services_business", "business_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Service {self.name} {self.duration_min}min {self.price}>"
