"""Tests de los recordatorios de cita."""
from __future__ import annotations

from datetime import datetime, timedelta

from app.models import Appointment, AppointmentStatus, Business, Customer
from app.reminders import send_due_reminders


async def _make_appt(db_session, seed, *, hours_ahead: float) -> Appointment:
    customer = Customer(
        business_id=seed.business_id, phone="+34600111222", name="Marta"
    )
    db_session.add(customer)
    await db_session.flush()
    start = datetime.now() + timedelta(hours=hours_ahead)
    appt = Appointment(
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        resource_id=seed.resource_ids[0],
        customer_id=customer.id,
        start_at=start,
        end_at=start + timedelta(minutes=30),
        block_start_at=start,
        block_end_at=start + timedelta(minutes=30),
        status=AppointmentStatus.CONFIRMED,
    )
    db_session.add(appt)
    await db_session.commit()
    return appt


def _collector():
    sent: list[tuple] = []

    async def send(to, template, params, pnid, lang):
        sent.append((to, template, params, pnid, lang))
        return True

    return sent, send


async def test_reminder_sent_once(db_session, seed):
    business = await db_session.get(Business, seed.business_id)
    business.whatsapp_phone_number_id = "111222333"
    appt = await _make_appt(db_session, seed, hours_ahead=2)

    sent, send = _collector()
    n = await send_due_reminders(db_session, send=send)
    assert n == 1
    assert len(sent) == 1
    to, _template, params, pnid, _lang = sent[0]
    assert to == "34600111222"  # sin '+'
    assert pnid == "111222333"
    assert "Marta" in params and "Corte" in params

    refreshed = await db_session.get(Appointment, appt.id)
    assert refreshed.reminder_sent_at is not None

    # Segundo barrido: no se reenvía.
    n2 = await send_due_reminders(db_session, send=send)
    assert n2 == 0


async def test_reminder_skips_outside_horizon(db_session, seed):
    business = await db_session.get(Business, seed.business_id)
    business.whatsapp_phone_number_id = "111222333"
    await _make_appt(db_session, seed, hours_ahead=48)  # fuera de las 24 h

    sent, send = _collector()
    n = await send_due_reminders(db_session, send=send)
    assert n == 0
    assert sent == []


async def test_reminder_skips_business_without_number(db_session, seed):
    # El negocio sembrado no tiene whatsapp_phone_number_id.
    await _make_appt(db_session, seed, hours_ahead=2)
    sent, send = _collector()
    n = await send_due_reminders(db_session, send=send)
    assert n == 0
    assert sent == []
