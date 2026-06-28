"""Tool: consulta de catálogo de servicios, duraciones y precios."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Service
from app.schemas.appointment import PriceInfo


async def list_services(
    session: AsyncSession, business_id: str, only_active: bool = True
) -> list[Service]:
    query = select(Service).where(Service.business_id == business_id)
    if only_active:
        query = query.where(Service.active.is_(True))
    return list((await session.scalars(query.order_by(Service.name))).all())


async def get_pricing(
    session: AsyncSession, business_id: str
) -> list[PriceInfo]:
    """Devuelve nombre, duración y precio de cada servicio activo."""
    services = await list_services(session, business_id, only_active=True)
    return [
        PriceInfo(
            service_id=s.id,
            name=s.name,
            duration_min=s.duration_min,
            price=s.price,
        )
        for s in services
    ]
