"""Tools de la lista de espera: alta, listado, baja y emparejado con un hueco."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import WaitlistEntry, WaitlistStatus


async def add_to_waitlist(
    session: AsyncSession,
    *,
    business_id: str,
    phone: str,
    service_id: str,
    name: str | None = None,
    resource_id: str | None = None,
    desired_date: date | None = None,
) -> WaitlistEntry:
    """Apunta a un cliente en la lista de espera (deduplica si ya estaba)."""
    existing = await session.scalar(
        select(WaitlistEntry).where(
            WaitlistEntry.business_id == business_id,
            WaitlistEntry.customer_phone == phone,
            WaitlistEntry.service_id == service_id,
            WaitlistEntry.status == WaitlistStatus.WAITING,
        )
    )
    if existing is not None:
        # Actualiza la preferencia por si cambió.
        existing.resource_id = resource_id
        existing.desired_date = desired_date
        if name and not existing.customer_name:
            existing.customer_name = name
        await session.flush()
        return existing

    entry = WaitlistEntry(
        business_id=business_id,
        customer_phone=phone,
        customer_name=name,
        service_id=service_id,
        resource_id=resource_id,
        desired_date=desired_date,
    )
    session.add(entry)
    await session.flush()
    return entry


async def list_waitlist(
    session: AsyncSession, business_id: str, *, only_waiting: bool = True
) -> list[WaitlistEntry]:
    query = select(WaitlistEntry).where(WaitlistEntry.business_id == business_id)
    if only_waiting:
        query = query.where(WaitlistEntry.status == WaitlistStatus.WAITING)
    return list((await session.scalars(query.order_by(WaitlistEntry.created_at))).all())


async def remove_from_waitlist(
    session: AsyncSession, business_id: str, entry_id: str
) -> bool:
    entry = await session.get(WaitlistEntry, entry_id)
    if entry is None or entry.business_id != business_id:
        return False
    await session.delete(entry)
    await session.flush()
    return True


async def match_for_freed_slot(
    session: AsyncSession,
    *,
    business_id: str,
    service_id: str,
    resource_id: str | None,
    day: date,
) -> WaitlistEntry | None:
    """Primer cliente en espera (el más antiguo) que encaja con un hueco liberado.

    Encaja si: mismo servicio, su día deseado es ese o cualquiera, y su profesional
    preferido es ese o cualquiera.
    """
    query = (
        select(WaitlistEntry)
        .where(
            WaitlistEntry.business_id == business_id,
            WaitlistEntry.service_id == service_id,
            WaitlistEntry.status == WaitlistStatus.WAITING,
        )
        .order_by(WaitlistEntry.created_at)
    )
    for entry in (await session.scalars(query)).all():
        if entry.desired_date is not None and entry.desired_date != day:
            continue
        if entry.resource_id is not None and entry.resource_id != resource_id:
            continue
        return entry
    return None
