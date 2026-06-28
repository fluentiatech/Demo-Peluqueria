"""Lógica de calendario compartida por las tools de disponibilidad y reserva.

Resuelve los tramos horarios efectivos de un recurso un día concreto, cruzando:
  - el horario del negocio (con festivos/cierres como excepción por fecha),
  - el horario propio del recurso (si lo tiene),
y deja fuera de aquí los descuentos por citas y ausencias, que se aplican sobre
intervalos de `datetime` en cada tool.
"""
from __future__ import annotations

from datetime import date, datetime, time
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BusinessClosure
from app.models.associations import service_resource

if TYPE_CHECKING:
    from app.models import Business, Resource

# Un par (inicio, fin) de horas dentro de un mismo día.
TimeInterval = tuple[time, time]


def parse_hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def _to_min(t: time) -> int:
    return t.hour * 60 + t.minute


def _from_min(total: int) -> time:
    return time(total // 60, total % 60)


def weekly_intervals(hours: dict[str, list], day: date) -> list[TimeInterval]:
    """Tramos definidos en un horario semanal para el día de la semana de `day`."""
    raw = (hours or {}).get(str(day.weekday()), [])
    return [(parse_hhmm(a), parse_hhmm(b)) for a, b in raw]


def intersect_intervals(
    a: list[TimeInterval], b: list[TimeInterval]
) -> list[TimeInterval]:
    """Intersección de dos listas de tramos horarios."""
    out: list[TimeInterval] = []
    for a0, a1 in a:
        for b0, b1 in b:
            lo = max(_to_min(a0), _to_min(b0))
            hi = min(_to_min(a1), _to_min(b1))
            if lo < hi:
                out.append((_from_min(lo), _from_min(hi)))
    return sorted(out)


def business_day_intervals(
    business: Business, closure: BusinessClosure | None, day: date
) -> list[TimeInterval]:
    """Tramos del negocio para `day`, aplicando la excepción de fecha si existe."""
    if closure is not None:
        if closure.is_closed:
            return []
        return [(parse_hhmm(a), parse_hhmm(b)) for a, b in (closure.custom_hours or [])]
    return weekly_intervals(business.opening_hours, day)


def resource_day_intervals(
    business_intervals: list[TimeInterval], resource: Resource, day: date
) -> list[TimeInterval]:
    """Tramos efectivos de un recurso: negocio ∩ horario propio (si lo tiene)."""
    if resource.working_hours:
        own = weekly_intervals(resource.working_hours, day)
        return intersect_intervals(business_intervals, own)
    return business_intervals


def overlaps(a0: datetime, a1: datetime, b0: datetime, b1: datetime) -> bool:
    """¿Se solapan los intervalos [a0,a1) y [b0,b1)?"""
    return a0 < b1 and b0 < a1


async def closures_by_date(
    session: AsyncSession, business_id: str, date_from: date, date_to: date
) -> dict[date, BusinessClosure]:
    """Excepciones de calendario del negocio en el rango, indexadas por fecha."""
    rows = (
        await session.scalars(
            select(BusinessClosure).where(
                BusinessClosure.business_id == business_id,
                BusinessClosure.date >= date_from,
                BusinessClosure.date <= date_to,
            )
        )
    ).all()
    return {c.date: c for c in rows}


async def eligible_resource_ids(
    session: AsyncSession, service_id: str
) -> set[str] | None:
    """Ids de recursos cualificados para un servicio.

    Devuelve `None` si el servicio no tiene restricción (lo hace cualquiera);
    en otro caso, el conjunto de recursos habilitados.
    """
    rows = (
        await session.scalars(
            select(service_resource.c.resource_id).where(
                service_resource.c.service_id == service_id
            )
        )
    ).all()
    return set(rows) if rows else None
