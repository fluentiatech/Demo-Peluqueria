"""Recordatorios de cita.

Barre las citas próximas que aún no se han recordado y envía una plantilla de
WhatsApp (Meta exige plantilla aprobada fuera de la ventana de 24 h). Pensado
para ejecutarse periódicamente (cron / scheduler); ver `scripts/send_reminders.py`.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.replies import fmt_slot
from app.config import settings
from app.integrations.whatsapp import send_template
from app.models import Appointment, AppointmentStatus, Business, Customer, Service

logger = logging.getLogger("agente-citas.reminders")

# (to, template, params, phone_number_id, lang) -> enviado
SendTemplate = Callable[[str, str, list[str], str, str], Awaitable[bool]]

_ACTIVE = (AppointmentStatus.PENDING, AppointmentStatus.CONFIRMED)


def _digits(phone: str) -> str:
    return phone.lstrip("+")


async def send_due_reminders(
    session: AsyncSession,
    *,
    send: SendTemplate | None = None,
    now: datetime | None = None,
) -> int:
    """Envía los recordatorios pendientes. Devuelve cuántos se enviaron."""
    send = send or send_template
    now = now or datetime.now()
    horizon = now + timedelta(hours=settings.reminder_hours_before)

    appts = (
        await session.scalars(
            select(Appointment).where(
                Appointment.status.in_(_ACTIVE),
                Appointment.reminder_sent_at.is_(None),
                Appointment.start_at > now,
                Appointment.start_at <= horizon,
            )
        )
    ).all()

    sent = 0
    for appt in appts:
        business = await session.get(Business, appt.business_id)
        customer = await session.get(Customer, appt.customer_id)
        service = await session.get(Service, appt.service_id)
        if business is None or customer is None or service is None:
            continue
        if not business.whatsapp_phone_number_id:
            logger.warning("Negocio %s sin phone_number_id; no se recuerda", business.id)
            continue

        params = [customer.name or "", service.name, fmt_slot(appt.start_at)]
        ok = await send(
            _digits(customer.phone),
            settings.reminder_template,
            params,
            business.whatsapp_phone_number_id,
            settings.reminder_lang,
        )
        # Marca como recordado si se envió, o si estamos en desarrollo sin token
        # (no-op), para no reintentar en bucle. Un fallo real (token activo) se
        # deja pendiente para el siguiente barrido.
        if ok or not settings.whatsapp_access_token:
            appt.reminder_sent_at = now
            sent += 1

    await session.commit()
    return sent
