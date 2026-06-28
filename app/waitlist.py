"""Relleno automático de cancelaciones desde la lista de espera.

Procesa los eventos `slot_freed` (que emite la cancelación/reprogramación) y, por
cada hueco liberado, busca al primero en espera que encaje y le **ofrece el hueco
por WhatsApp**. Además deja su conversación lista para que un simple "sí" lo
reserve. Desacoplado e idempotente (vía `notified_at`), como recordatorios/avisos.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import replies
from app.config import settings
from app.models import (
    Business,
    Conversation,
    ConversationState,
    EventLog,
    Service,
    WaitlistStatus,
)
from app.tools.waitlist import match_for_freed_slot

logger = logging.getLogger("agente-citas.waitlist")

SendText = Callable[[str, str, str], Awaitable[bool]]


def _digits(phone: str) -> str:
    return phone.lstrip("+")


def _offer_text(business: Business, name: str | None, service_name: str, label: str) -> str:
    first = replies._first_name(name)
    hola = f"¡Hola, {first}!" if first else "¡Hola!"
    emo = "🎉 " if business.use_emojis else ""
    intro = f" Soy {business.assistant_name}." if business.assistant_name else ""
    return (
        f"{emo}{hola}{intro} Se ha liberado un hueco para {service_name} "
        f"el {label}. ¿Te lo reservo? Responde *sí* y es tuyo."
    )


async def _prime_conversation(
    session: AsyncSession,
    business_id: str,
    phone: str,
    ctx: dict,
) -> None:
    """Deja la conversación del cliente en CONFIRMING con el hueco pre-cargado,
    de modo que su próximo 'sí' lo reserve por el flujo normal."""
    convo = await session.scalar(
        select(Conversation).where(
            Conversation.business_id == business_id,
            Conversation.customer_phone == phone,
        )
    )
    if convo is None:
        convo = Conversation(business_id=business_id, customer_phone=phone)
        session.add(convo)
    convo.state = ConversationState.CONFIRMING
    convo.context = ctx
    await session.flush()


async def process_freed_slots(
    session: AsyncSession,
    *,
    send: SendText | None = None,
    now: datetime | None = None,
) -> int:
    """Ofrece los huecos liberados a la lista de espera. Devuelve cuántos ofreció."""
    from app.integrations.whatsapp import send_text

    send = send or send_text
    now = now or datetime.now(UTC)
    since = now - timedelta(hours=settings.alert_lookback_hours)

    events = (
        await session.scalars(
            select(EventLog).where(
                EventLog.type == "slot_freed",
                EventLog.notified_at.is_(None),
                EventLog.created_at >= since,
            )
        )
    ).all()

    offered = 0
    for ev in events:
        p = ev.payload or {}
        try:
            start_at = datetime.fromisoformat(p["start_at"])
        except (KeyError, ValueError):
            ev.notified_at = now  # evento corrupto: márcalo procesado y sigue
            continue

        entry = await match_for_freed_slot(
            session,
            business_id=ev.business_id or "",
            service_id=p.get("service_id", ""),
            resource_id=p.get("resource_id"),
            day=start_at.date(),
        )
        if entry is None:
            ev.notified_at = now  # nadie en espera para ese hueco
            continue

        business = await session.get(Business, ev.business_id)
        service = await session.get(Service, entry.service_id)
        if business is None or service is None:
            ev.notified_at = now
            continue

        label = replies.fmt_slot(start_at)
        # Prepara la conversación para que "sí" reserve este hueco concreto.
        await _prime_conversation(
            session,
            business.id,
            entry.customer_phone,
            {
                "action": "book",
                "service_id": entry.service_id,
                "service_name": service.name,
                "name": entry.customer_name,
                "from_waitlist": True,
                "chosen": {
                    "start_at": start_at.isoformat(),
                    "resource_id": p.get("resource_id"),
                    "label": label,
                },
            },
        )

        pnid = business.whatsapp_phone_number_id
        sent = False
        if pnid and settings.whatsapp_access_token:
            sent = await send(
                _digits(entry.customer_phone),
                _offer_text(business, entry.customer_name, service.name, label),
                pnid,
            )

        # En desarrollo (sin token) damos el evento por procesado igualmente.
        if sent or not settings.whatsapp_access_token:
            entry.status = WaitlistStatus.NOTIFIED
            entry.notified_at = now
            ev.notified_at = now
            offered += 1
        else:
            logger.warning("No se pudo ofrecer el hueco a %s; se reintentará", entry.id)

    await session.commit()
    return offered
