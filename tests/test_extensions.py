"""Tests de las extensiones del modelo: capacidad, horarios, ausencias, festivos, buffers."""
from __future__ import annotations

from datetime import datetime, time

import pytest

from app.models import BusinessClosure, Resource, Service, TimeOff
from app.tools import (
    BookingError,
    OutOfHoursError,
    assign_service_resources,
    book_appointment,
    check_availability,
)
from tests.conftest import next_weekday


def _at(day, hour, minute=0) -> datetime:
    return datetime.combine(day, time(hour, minute))


# --------------------------------------------------------------------------- #
#  Capacidad servicio↔profesional
# --------------------------------------------------------------------------- #
async def test_capability_restricts_to_qualified_resource(db_session, seed):
    # Solo el recurso 0 puede hacer "Corte".
    await assign_service_resources(
        db_session, seed.business_id, seed.service_ids["Corte"], [seed.resource_ids[0]]
    )
    await db_session.commit()

    day = next_weekday(0)
    slots = await check_availability(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        date_from=day,
        date_to=day,
    )
    assert {s.resource_id for s in slots} == {seed.resource_ids[0]}


async def test_capability_blocks_unqualified_resource_booking(db_session, seed):
    await assign_service_resources(
        db_session, seed.business_id, seed.service_ids["Corte"], [seed.resource_ids[0]]
    )
    await db_session.commit()
    with pytest.raises(BookingError):
        await book_appointment(
            db_session,
            business_id=seed.business_id,
            service_id=seed.service_ids["Corte"],
            start_at=_at(next_weekday(0), 10),
            phone="+34600000100",
            resource_id=seed.resource_ids[1],  # no cualificado
        )


# --------------------------------------------------------------------------- #
#  Buffer de preparación/limpieza
# --------------------------------------------------------------------------- #
async def test_buffer_spaces_consecutive_slots(db_session, seed):
    service = await db_session.get(Service, seed.service_ids["Corte"])
    service.buffer_after_min = 15
    await db_session.commit()

    day = next_weekday(0)
    await book_appointment(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        start_at=_at(day, 10),
        phone="+34600000101",
        resource_id=seed.resource_ids[0],
    )
    await db_session.commit()

    slots = await check_availability(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        date_from=day,
        date_to=day,
        resource_id=seed.resource_ids[0],
    )
    starts = {s.start_at for s in slots}
    # El corte de 10:00–10:30 + 15 min de limpieza bloquea hasta las 10:45.
    assert _at(day, 10, 30) not in starts
    assert _at(day, 10, 45) in starts


# --------------------------------------------------------------------------- #
#  Festivos / cierres del negocio
# --------------------------------------------------------------------------- #
async def test_closure_makes_day_unavailable(db_session, seed):
    day = next_weekday(2)
    db_session.add(
        BusinessClosure(business_id=seed.business_id, date=day, is_closed=True)
    )
    await db_session.commit()

    slots = await check_availability(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        date_from=day,
        date_to=day,
    )
    assert slots == []
    with pytest.raises(OutOfHoursError):
        await book_appointment(
            db_session,
            business_id=seed.business_id,
            service_id=seed.service_ids["Corte"],
            start_at=_at(day, 10),
            phone="+34600000102",
        )


async def test_special_opening_hours_override(db_session, seed):
    day = next_weekday(3)
    db_session.add(
        BusinessClosure(
            business_id=seed.business_id,
            date=day,
            is_closed=False,
            custom_hours=[["16:00", "18:00"]],
        )
    )
    await db_session.commit()

    slots = await check_availability(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        date_from=day,
        date_to=day,
    )
    assert slots
    assert all(s.start_at.time() >= time(16, 0) for s in slots)
    assert all(s.end_at.time() <= time(18, 0) for s in slots)
    # Reservar a las 10:00 (fuera del horario especial) falla.
    with pytest.raises(OutOfHoursError):
        await book_appointment(
            db_session,
            business_id=seed.business_id,
            service_id=seed.service_ids["Corte"],
            start_at=_at(day, 10),
            phone="+34600000103",
        )


# --------------------------------------------------------------------------- #
#  Ausencias de recursos
# --------------------------------------------------------------------------- #
async def test_time_off_excludes_slots(db_session, seed):
    day = next_weekday(0)
    db_session.add(
        TimeOff(
            business_id=seed.business_id,
            resource_id=seed.resource_ids[0],
            start_at=_at(day, 10),
            end_at=_at(day, 12),
            reason="Médico",
        )
    )
    await db_session.commit()

    slots = await check_availability(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        date_from=day,
        date_to=day,
        resource_id=seed.resource_ids[0],
    )
    # Ningún hueco del recurso 0 cae dentro de la ausencia (10:00–12:00).
    assert all(not (time(10, 0) <= s.start_at.time() < time(12, 0)) for s in slots)

    with pytest.raises(BookingError):
        await book_appointment(
            db_session,
            business_id=seed.business_id,
            service_id=seed.service_ids["Corte"],
            start_at=_at(day, 10, 30),
            phone="+34600000104",
            resource_id=seed.resource_ids[0],
        )


# --------------------------------------------------------------------------- #
#  Horario propio del recurso
# --------------------------------------------------------------------------- #
async def test_resource_working_hours_narrow_availability(db_session, seed):
    day = next_weekday(0)  # weekday 0
    resource = await db_session.get(Resource, seed.resource_ids[0])
    resource.working_hours = {"0": [["09:00", "11:00"]]}
    await db_session.commit()

    slots = await check_availability(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        date_from=day,
        date_to=day,
        resource_id=seed.resource_ids[0],
    )
    assert slots
    # El recurso 0 solo trabaja hasta las 11:00 ese día.
    assert all(s.end_at.time() <= time(11, 0) for s in slots)


async def test_booking_out_of_hours_is_rejected(db_session, seed):
    with pytest.raises(OutOfHoursError):
        await book_appointment(
            db_session,
            business_id=seed.business_id,
            service_id=seed.service_ids["Corte"],
            start_at=_at(next_weekday(0), 20),  # cierra a las 14:00
            phone="+34600000105",
        )
