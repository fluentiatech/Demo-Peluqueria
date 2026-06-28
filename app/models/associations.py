"""Tablas de asociación (relaciones N:M sin columnas propias)."""
from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Table

from app.models.base import Base

# Qué recurso/profesional está cualificado para realizar qué servicio.
# Regla de negocio: si un servicio NO tiene filas aquí, lo puede hacer cualquier
# recurso activo (retrocompatibilidad). Si tiene filas, solo esos recursos.
service_resource = Table(
    "service_resource",
    Base.metadata,
    Column(
        "service_id",
        ForeignKey("services.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "resource_id",
        ForeignKey("resources.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)
