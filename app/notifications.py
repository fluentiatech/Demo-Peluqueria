"""Avisos al negocio: handoffs pendientes y errores del agente.

Barre los eventos `handoff`/`error` aún no notificados y envía un aviso al
teléfono del negocio (`notify_phone`). Idempotente vía `EventLog.notified_at`,
decoupled del camino de la petición (cron, como recordatorios y purga). Así el
negocio se entera cuando hace falta una persona, sin vigilar el sistema.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Business, EventLog

logger = logging.getLogger("agente-citas.notifications")

SendText = Callable[[str, str, str], Awaitable[bool]]
_ALERT_TYPES = ("handoff", "error")


def _digits(phone: str) -> str:
    return phone.lstrip("+")


def _message(handoffs: int, errors: int, hours: int) -> str:
    partes = []
    if handoffs:
        partes.append(f"{handoffs} conversación(es) esperando a una persona")
    if errors:
        partes.append(f"{errors} error(es) del agente")
    detalle = " y ".join(partes) if partes else "actividad que revisar"
    return f"⚠️ Tu agente necesita atención: {detalle} en las últimas {hours} h."


async def send_pending_alerts(
    session: AsyncSession,
    *,
    send: SendText | None = None,
    now: datetime | None = None,
) -> int:
    """Notifica los eventos pendientes agrupados por negocio. Devuelve cuántos marcó."""
    from app.integrations.whatsapp import send_text

    send = send or send_text
    now = now or datetime.now(UTC)
    since = now - timedelta(hours=settings.alert_lookback_hours)

    events = (
        await session.scalars(
            select(EventLog).where(
                EventLog.type.in_(_ALERT_TYPES),
                EventLog.notified_at.is_(None),
                EventLog.created_at >= since,
            )
        )
    ).all()

    by_business: dict[str | None, list[EventLog]] = defaultdict(list)
    for e in events:
        by_business[e.business_id].append(e)

    marked = 0
    for business_id, evs in by_business.items():
        business = (
            await session.get(Business, business_id) if business_id else None
        )
        notify = business.notify_phone if business else None
        pnid = business.whatsapp_phone_number_id if business else None
        can_send = bool(notify and pnid)

        ok = False
        if notify and pnid:
            handoffs = sum(1 for e in evs if e.type == "handoff")
            errors = sum(1 for e in evs if e.type == "error")
            ok = await send(
                _digits(notify),
                _message(handoffs, errors, settings.alert_lookback_hours),
                pnid,
            )

        # Marca como notificado si se envió, si no hay a quién avisar, o en
        # desarrollo (sin token). Un fallo real con token activo se deja pendiente.
        if ok or not can_send or not settings.whatsapp_access_token:
            for e in evs:
                e.notified_at = now
            marked += len(evs)
        else:
            logger.warning("No se pudo avisar al negocio %s; se reintentará", business_id)

    await session.commit()
    return marked
