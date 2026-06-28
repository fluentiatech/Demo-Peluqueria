"""Tool: capacidad servicio↔recurso (qué profesional realiza qué servicio)."""
from __future__ import annotations

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Resource, Service
from app.models.associations import service_resource


async def list_service_resources(
    session: AsyncSession, business_id: str, service_id: str
) -> list[str]:
    """Ids de los recursos cualificados para un servicio (vacío = cualquiera)."""
    service = await session.get(Service, service_id)
    if service is None or service.business_id != business_id:
        raise ValueError("Servicio no encontrado")
    rows = (
        await session.scalars(
            select(service_resource.c.resource_id).where(
                service_resource.c.service_id == service_id
            )
        )
    ).all()
    return list(rows)


async def assign_service_resources(
    session: AsyncSession,
    business_id: str,
    service_id: str,
    resource_ids: list[str],
) -> list[str]:
    """Fija (reemplaza) el conjunto de recursos cualificados para un servicio.

    Lista vacía = sin restricción (lo puede hacer cualquier recurso activo).
    """
    service = await session.get(Service, service_id)
    if service is None or service.business_id != business_id:
        raise ValueError("Servicio no encontrado")

    # Valida que todos los recursos existan y pertenezcan al mismo negocio.
    if resource_ids:
        valid = set(
            (
                await session.scalars(
                    select(Resource.id).where(
                        Resource.business_id == business_id,
                        Resource.id.in_(resource_ids),
                    )
                )
            ).all()
        )
        missing = set(resource_ids) - valid
        if missing:
            raise ValueError(f"Recursos no válidos para el negocio: {sorted(missing)}")

    await session.execute(
        delete(service_resource).where(
            service_resource.c.service_id == service_id
        )
    )
    if resource_ids:
        await session.execute(
            insert(service_resource),
            [{"service_id": service_id, "resource_id": rid} for rid in resource_ids],
        )
    await session.flush()
    return resource_ids
