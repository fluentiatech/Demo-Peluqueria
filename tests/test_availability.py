"""Tests de la tool de disponibilidad."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.tools import book_appointment, check_availability
from tests.conftest import next_weekday

_MADRID = ZoneInfo("Europe/Madrid")


async def test_slots_within_opening_hours(db_session, seed):
    day = next_weekday(0)  # lunes, 09:00–14:00
    slots = await check_availability(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        date_from=day,
        date_to=day,
    )
    assert slots, "debería haber huecos"
    for s in slots:
        assert time(9, 0) <= s.start_at.time()
        # Un corte de 30 min: el último inicio válido es 13:30.
        assert s.end_at.time() <= time(14, 0)
    assert slots[0].start_at == datetime.combine(day, time(9, 0), tzinfo=_MADRID)


async def test_booked_slot_is_excluded(db_session, seed):
    day = next_weekday(1)
    start = datetime.combine(day, time(9, 0), tzinfo=_MADRID)
    await book_appointment(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        start_at=start,
        phone="+34600000001",
        resource_id=seed.resource_ids[0],
    )
    # Ocupa también el otro recurso a las 9:00 para que esa franja desaparezca.
    await book_appointment(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        start_at=start,
        phone="+34600000002",
        resource_id=seed.resource_ids[1],
    )
    await db_session.commit()

    slots = await check_availability(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        date_from=day,
        date_to=day,
    )
    assert all(s.start_at != start for s in slots)


async def test_past_dates_yield_no_slots(db_session, seed):
    past = datetime.now().date() - timedelta(days=7)
    slots = await check_availability(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        date_from=past,
        date_to=past,
    )
    assert slots == []


async def test_unknown_service_raises(db_session, seed):
    with pytest.raises(ValueError):
        await check_availability(
            db_session,
            business_id=seed.business_id,
            service_id="no-existe",
            date_from=next_weekday(0),
            date_to=next_weekday(0),
        )
