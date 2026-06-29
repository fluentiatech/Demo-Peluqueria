"""Tool: cálculo de huecos disponibles.

La disponibilidad NUNCA sale de una búsqueda semántica: es un cálculo exacto
sobre el horario efectivo de cada recurso menos lo ya ocupado.

Se tiene en cuenta:
  - capacidad: solo recursos cualificados para el servicio,
  - horario del negocio con festivos/cierres por fecha,
  - horario propio del recurso (intersección),
  - citas existentes (con su buffer) y ausencias del recurso,
  - buffer de preparación/limpieza del servicio.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import timez
from app.models import (
    Appointment,
    AppointmentStatus,
    Business,
    Resource,
    Service,
    TimeOff,
)
from app.schemas.appointment import Slot
from app.tools.scheduling import (
    TimeInterval,
    business_day_intervals,
    closures_by_date,
    eligible_resource_ids,
    overlaps,
    resource_day_intervals,
)

# Estados que ocupan el recurso (los demás liberan el hueco).
_BLOCKING_STATUSES = (
    AppointmentStatus.PENDING,
    AppointmentStatus.CONFIRMED,
    AppointmentStatus.COMPLETED,
)


def _fits(intervals: list[TimeInterval], day: date, start: datetime, end: datetime) -> bool:
    """¿Cabe [start, end] dentro de alguno de los tramos abiertos del día?"""
    for i0, i1 in intervals:
        if timez.local(day, i0) <= start and end <= timez.local(day, i1):
            return True
    return False


async def check_availability(
    session: AsyncSession,
    business_id: str,
    service_id: str,
    date_from: date,
    date_to: date,
    resource_id: str | None = None,
    limit: int = 50,
) -> list[Slot]:
    """Lista los huecos libres para `service_id` entre dos fechas (inclusive)."""
    business = await session.get(Business, business_id)
    if business is None:
        raise ValueError("Negocio no encontrado")

    service = await session.get(Service, service_id)
    if service is None or service.business_id != business_id:
        raise ValueError("Servicio no encontrado")

    duration = timedelta(minutes=service.duration_min)
    before = timedelta(minutes=service.buffer_before_min)
    after = timedelta(minutes=service.buffer_after_min)
    step = timedelta(minutes=business.slot_granularity_min or 15)

    # Recursos candidatos: activos, del negocio, cualificados y (opcional) el pedido.
    eligible = await eligible_resource_ids(session, service_id)
    res_query = select(Resource).where(
        Resource.business_id == business_id, Resource.active.is_(True)
    )
    if resource_id:
        res_query = res_query.where(Resource.id == resource_id)
    if eligible is not None:
        res_query = res_query.where(Resource.id.in_(eligible))
    resources = (await session.scalars(res_query)).all()
    if not resources:
        return []

    range_start = timez.local(date_from, time.min)
    range_end = timez.local(date_to + timedelta(days=1), time.min)

    # Citas existentes en el rango → ventanas de bloqueo por recurso.
    appts = (
        await session.scalars(
            select(Appointment).where(
                Appointment.business_id == business_id,
                Appointment.status.in_(_BLOCKING_STATUSES),
                Appointment.block_start_at < range_end,
                Appointment.block_end_at > range_start,
            )
        )
    ).all()
    # Normalizamos a hora local consciente de zona: la BD puede devolver naive
    # (SQLite) o aware en UTC (Postgres); así las comparaciones nunca mezclan.
    busy: dict[str, list[tuple[datetime, datetime]]] = {}
    for a in appts:
        busy.setdefault(a.resource_id, []).append(
            (timez.to_local(a.block_start_at), timez.to_local(a.block_end_at))
        )

    # Ausencias en el rango por recurso.
    offs = (
        await session.scalars(
            select(TimeOff).where(
                TimeOff.business_id == business_id,
                TimeOff.start_at < range_end,
                TimeOff.end_at > range_start,
            )
        )
    ).all()
    for o in offs:
        busy.setdefault(o.resource_id, []).append(
            (timez.to_local(o.start_at), timez.to_local(o.end_at))
        )

    closures = await closures_by_date(session, business_id, date_from, date_to)
    now = timez.now()
    slots: list[Slot] = []

    day = date_from
    while day <= date_to and len(slots) < limit:
        biz_intervals = business_day_intervals(business, closures.get(day), day)
        if biz_intervals:
            res_intervals = {
                res.id: resource_day_intervals(biz_intervals, res, day)
                for res in resources
            }
            for ini, fin in biz_intervals:
                cursor = timez.local(day, ini)
                interval_end = timez.local(day, fin)
                while cursor + duration <= interval_end:
                    svc_end = cursor + duration
                    if cursor >= now:
                        block0, block1 = cursor - before, svc_end + after
                        for res in resources:
                            if not _fits(res_intervals[res.id], day, cursor, svc_end):
                                continue
                            taken = busy.get(res.id, [])
                            if any(overlaps(block0, block1, b0, b1) for b0, b1 in taken):
                                continue
                            slots.append(
                                Slot(
                                    resource_id=res.id,
                                    resource_name=res.name,
                                    start_at=cursor,
                                    end_at=svc_end,
                                )
                            )
                            break
                    if len(slots) >= limit:
                        break
                    cursor += step
                if len(slots) >= limit:
                    break
        day += timedelta(days=1)

    return slots


async def free_resources_at(
    session: AsyncSession,
    business_id: str,
    service_id: str,
    start_at: datetime,
) -> list[Slot]:
    """Profesionales cualificados libres para EMPEZAR el servicio justo a `start_at`.

    A diferencia de `check_availability` (que devuelve un hueco por hora), aquí
    devolvemos TODOS los recursos que pueden atender a esa hora exacta: sirve para
    proponer «otro profesional libre a la hora que pediste».
    """
    business = await session.get(Business, business_id)
    service = await session.get(Service, service_id)
    if business is None or service is None or service.business_id != business_id:
        return []

    start = timez.to_local(timez.aware(start_at))
    if start < timez.now():
        return []
    day = start.date()
    svc_end = start + timedelta(minutes=service.duration_min)
    block0 = start - timedelta(minutes=service.buffer_before_min)
    block1 = svc_end + timedelta(minutes=service.buffer_after_min)

    eligible = await eligible_resource_ids(session, service_id)
    res_query = select(Resource).where(
        Resource.business_id == business_id, Resource.active.is_(True)
    )
    if eligible is not None:
        res_query = res_query.where(Resource.id.in_(eligible))
    resources = (await session.scalars(res_query)).all()
    if not resources:
        return []

    closures = await closures_by_date(session, business_id, day, day)
    biz_intervals = business_day_intervals(business, closures.get(day), day)
    if not biz_intervals:
        return []

    range_start = timez.local(day, time.min)
    range_end = timez.local(day + timedelta(days=1), time.min)
    busy: dict[str, list[tuple[datetime, datetime]]] = {}
    appts = (
        await session.scalars(
            select(Appointment).where(
                Appointment.business_id == business_id,
                Appointment.status.in_(_BLOCKING_STATUSES),
                Appointment.block_start_at < range_end,
                Appointment.block_end_at > range_start,
            )
        )
    ).all()
    for a in appts:
        busy.setdefault(a.resource_id, []).append(
            (timez.to_local(a.block_start_at), timez.to_local(a.block_end_at))
        )
    offs = (
        await session.scalars(
            select(TimeOff).where(
                TimeOff.business_id == business_id,
                TimeOff.start_at < range_end,
                TimeOff.end_at > range_start,
            )
        )
    ).all()
    for o in offs:
        busy.setdefault(o.resource_id, []).append(
            (timez.to_local(o.start_at), timez.to_local(o.end_at))
        )

    out: list[Slot] = []
    for res in resources:
        intervals = resource_day_intervals(biz_intervals, res, day)
        if not _fits(intervals, day, start, svc_end):
            continue
        if any(overlaps(block0, block1, b0, b1) for b0, b1 in busy.get(res.id, [])):
            continue
        out.append(
            Slot(resource_id=res.id, resource_name=res.name,
                 start_at=start, end_at=svc_end)
        )
    return out
