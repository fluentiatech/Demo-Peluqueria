"""Orquesta un webhook entrante de WhatsApp: enruta, deduplica, responde y envía.

Se inyectan el cliente LLM y la función de envío para poder probarlo sin red ni
credenciales reales.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.flow import handle_message
from app.agent.llm import LLMClient
from app.config import settings
from app.integrations.whatsapp import IncomingMessage, parse_incoming
from app.models import Business, EventLog, InboundMessage

logger = logging.getLogger("agente-citas.handler")

SendFunc = Callable[[str, str, str], Awaitable[bool]]


async def _resolve_business(
    session: AsyncSession, phone_number_id: str
) -> Business | None:
    """Enruta el mensaje a su negocio por el número de WhatsApp.

    Si no hay coincidencia pero solo existe un negocio (despliegue single-tenant),
    se usa ese.
    """
    if phone_number_id:
        business = await session.scalar(
            select(Business).where(
                Business.whatsapp_phone_number_id == phone_number_id
            )
        )
        if business is not None:
            return business
    candidates = (await session.scalars(select(Business).limit(2))).all()
    return candidates[0] if len(candidates) == 1 else None


async def _claim_message(
    session: AsyncSession, business_id: str, msg: IncomingMessage
) -> bool:
    """Reclama el mensaje de forma atómica. False si ya estaba procesado.

    Inserta en `inbound_messages` dentro de un savepoint; el UNIQUE
    (business_id, message_id) hace que una reentrega duplicada falle aquí, sin
    ventana de carrera. Sin message_id (caso raro) se procesa sin dedupe.
    """
    if not msg.message_id:
        return True
    try:
        async with session.begin_nested():
            session.add(
                InboundMessage(
                    business_id=business_id,
                    message_id=msg.message_id,
                    from_phone=msg.from_,
                )
            )
    except IntegrityError:
        return False
    return True


async def _handle_one(
    session: AsyncSession, msg: IncomingMessage, llm: LLMClient, send: SendFunc
) -> bool:
    business = await _resolve_business(session, msg.phone_number_id)
    if business is None:
        logger.warning("Mensaje sin negocio (phone_number_id=%s)", msg.phone_number_id)
        return False
    if not await _claim_message(session, business.id, msg):
        return False

    text = msg.text[: settings.max_inbound_chars]
    try:
        answer = await handle_message(
            session, business, msg.from_, text, llm, message_id=msg.message_id
        )
    except Exception:
        logger.exception("Fallo generando respuesta")
        session.add(
            EventLog(
                business_id=business.id,
                type="error",
                payload={"stage": "flow", "message_id": msg.message_id},
            )
        )
        return False

    # Una respuesta vacía (p. ej. en handoff) significa "no enviar".
    if answer:
        await send(msg.from_, answer[: settings.max_outbound_chars], msg.phone_number_id)
    return True


async def process_incoming(
    session: AsyncSession,
    payload: dict[str, Any],
    llm: LLMClient,
    send: SendFunc,
) -> int:
    """Procesa los mensajes de un payload (acotados). Devuelve cuántos se respondieron."""
    processed = 0
    # Cota anti-abuso: no procesamos un número desmesurado de mensajes por payload.
    for msg in parse_incoming(payload)[: settings.max_messages_per_payload]:
        if await _handle_one(session, msg, llm, send):
            processed += 1
    await session.commit()
    return processed
