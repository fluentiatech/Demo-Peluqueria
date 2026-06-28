"""Tests de la tool de reservas: idempotencia, anti-doble-reserva, cancelar, reprogramar."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.models import Appointment, AppointmentStatus, Service
from app.tools import (
    BookingError,
    SlotTakenError,
    book_appointment,
    cancel_appointment,
    reschedule_appointment,
)
from tests.conftest import next_weekday

_MADRID = ZoneInfo("Europe/Madrid")


def _future_start(weekday: int = 0, hour: int = 10) -> datetime:
    # Hora local del negocio (España), como en producción.
    return datetime.combine(next_weekday(weekday), time(hour, 0), tzinfo=_MADRID)


async def test_book_creates_pending_with_correct_end(db_session, seed):
    start = _future_start()
    appt = await book_appointment(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        start_at=start,
        phone="+34600000010",
        name="Ana",
    )
    assert appt.status == AppointmentStatus.PENDING
    assert appt.end_at == start + timedelta(minutes=30)
    assert appt.resource_id in seed.resource_ids


async def test_idempotency_returns_same_appointment(db_session, seed):
    start = _future_start()
    kwargs = {
        "business_id": seed.business_id,
        "service_id": seed.service_ids["Corte"],
        "start_at": start,
        "phone": "+34600000011",
        "idempotency_key": "wamid.ABC",
    }
    a1 = await book_appointment(db_session, **kwargs)
    await db_session.commit()
    a2 = await book_appointment(db_session, **kwargs)
    assert a1.id == a2.id


async def test_double_booking_same_resource_is_rejected(db_session, seed):
    start = _future_start()
    await book_appointment(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        start_at=start,
        phone="+34600000012",
        resource_id=seed.resource_ids[0],
    )
    await db_session.commit()
    with pytest.raises(SlotTakenError):
        await book_appointment(
            db_session,
            business_id=seed.business_id,
            service_id=seed.service_ids["Corte"],
            start_at=start,
            phone="+34600000013",
            resource_id=seed.resource_ids[0],
        )


async def test_auto_resource_balances_load(db_session, seed):
    """Sin profesional concreto, la cita va al que MENOS citas tiene ese día."""
    day = next_weekday(0)
    # El recurso 0 ya tiene una cita ese día (a otra hora).
    await book_appointment(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        start_at=datetime.combine(day, time(10, 0)),
        phone="+34600000200",
        resource_id=seed.resource_ids[0],
    )
    await db_session.commit()
    # Otra cita "me da igual" a otra hora → debe ir al recurso 1 (sin citas).
    appt = await book_appointment(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        start_at=datetime.combine(day, time(11, 0)),
        phone="+34600000201",
    )
    assert appt.resource_id == seed.resource_ids[1]


async def test_auto_resource_picks_free_one(db_session, seed):
    start = _future_start()
    a1 = await book_appointment(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        start_at=start,
        phone="+34600000014",
    )
    await db_session.commit()
    a2 = await book_appointment(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        start_at=start,
        phone="+34600000015",
    )
    assert a1.resource_id != a2.resource_id


async def test_overlapping_different_start_is_rejected(db_session, seed):
    """Un tinte (60 min) a las 10:00 choca con un corte a las 10:30 en el mismo recurso."""
    await book_appointment(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Tinte"],
        start_at=_future_start(hour=10),
        phone="+34600000016",
        resource_id=seed.resource_ids[0],
    )
    await db_session.commit()
    with pytest.raises(SlotTakenError):
        await book_appointment(
            db_session,
            business_id=seed.business_id,
            service_id=seed.service_ids["Corte"],
            start_at=_future_start(hour=10) + timedelta(minutes=30),
            phone="+34600000017",
            resource_id=seed.resource_ids[0],
        )


async def test_force_books_outside_hours_but_not_double(db_session, seed):
    """Alta manual: fuera de horario sí, pero el anti-doble-reserva se mantiene."""
    start = datetime.combine(next_weekday(0), time(22, 0))  # cerrado a esa hora
    appt = await book_appointment(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        start_at=start,
        phone="+34600000300",
        resource_id=seed.resource_ids[0],
        force=True,
    )
    assert appt.id is not None
    # El mismo recurso/hora sigue protegido aunque sea force.
    with pytest.raises(SlotTakenError):
        await book_appointment(
            db_session,
            business_id=seed.business_id,
            service_id=seed.service_ids["Corte"],
            start_at=start,
            phone="+34600000301",
            resource_id=seed.resource_ids[0],
            force=True,
        )


async def test_cannot_book_in_the_past(db_session, seed):
    past = datetime.now() - timedelta(days=1)
    with pytest.raises(BookingError):
        await book_appointment(
            db_session,
            business_id=seed.business_id,
            service_id=seed.service_ids["Corte"],
            start_at=past,
            phone="+34600000018",
        )


async def test_inactive_service_cannot_be_booked(db_session, seed):
    service = await db_session.get(Service, seed.service_ids["Corte"])
    service.active = False
    await db_session.commit()
    with pytest.raises(BookingError):
        await book_appointment(
            db_session,
            business_id=seed.business_id,
            service_id=seed.service_ids["Corte"],
            start_at=_future_start(),
            phone="+34600000019",
        )


async def test_cancel_deletes_and_frees_slot(db_session, seed):
    start = _future_start()
    appt = await book_appointment(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        start_at=start,
        phone="+34600000020",
        resource_id=seed.resource_ids[0],
    )
    await db_session.commit()

    assert await cancel_appointment(db_session, seed.business_id, appt.id) is True
    # Cancelar elimina la cita: el hueco queda libre.
    assert await db_session.get(Appointment, appt.id) is None


async def test_reschedule_moves_appointment(db_session, seed):
    appt = await book_appointment(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        start_at=_future_start(hour=10),
        phone="+34600000021",
        resource_id=seed.resource_ids[0],
    )
    await db_session.commit()
    new_start = _future_start(hour=12)
    moved = await reschedule_appointment(
        db_session, seed.business_id, appt.id, new_start
    )
    assert moved.start_at == new_start
    assert moved.end_at == new_start + timedelta(minutes=30)


async def test_reschedule_into_busy_slot_is_rejected(db_session, seed):
    a1 = await book_appointment(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        start_at=_future_start(hour=10),
        phone="+34600000022",
        resource_id=seed.resource_ids[0],
    )
    await book_appointment(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        start_at=_future_start(hour=11),
        phone="+34600000023",
        resource_id=seed.resource_ids[0],
    )
    await db_session.commit()
    with pytest.raises(SlotTakenError):
        await reschedule_appointment(
            db_session, seed.business_id, a1.id, _future_start(hour=11)
        )
