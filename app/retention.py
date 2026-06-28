"""Purga de datos operativos antiguos (escalabilidad + RGPD).

`inbound_messages` y `events_log` crecen indefinidamente y contienen PII
(teléfonos, textos de conversación). Esta limpieza periódica acota el tamaño de
las tablas y cumple la política de retención. Pensada para cron; ver
`scripts/purge_old.py`. NO toca citas ni clientes (histórico de negocio).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Conversation, ConversationState, EventLog, InboundMessage

logger = logging.getLogger("agente-citas.retention")


async def purge_old(
    session: AsyncSession,
    *,
    days: int | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """Borra mensajes entrantes, eventos y conversaciones inactivas antiguas."""
    days = days if days is not None else settings.retention_days
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)

    inbound = await session.execute(
        delete(InboundMessage).where(InboundMessage.created_at < cutoff)
    )
    events = await session.execute(
        delete(EventLog).where(EventLog.created_at < cutoff)
    )
    # Solo conversaciones inactivas (en IDLE) y sin tocar desde el corte.
    convos = await session.execute(
        delete(Conversation).where(
            Conversation.updated_at < cutoff,
            Conversation.state == ConversationState.IDLE,
        )
    )
    await session.commit()

    result = {
        "inbound_messages": getattr(inbound, "rowcount", 0) or 0,
        "events_log": getattr(events, "rowcount", 0) or 0,
        "conversations": getattr(convos, "rowcount", 0) or 0,
    }
    logger.info("Purga de retención (%d días): %s", days, result)
    return result
