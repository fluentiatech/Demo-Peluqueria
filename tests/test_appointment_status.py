"""Tests de confirmación de cita y snapshot de precio/duración."""
from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal

from app.models import Appointment, AppointmentStatus, Service
from app.tools import book_appointment
from tests.conftest import next_weekday


async def _book(db_session, seed, **kw):
    return await book_appointment(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        start_at=datetime.combine(next_weekday(0), time(10, 0)),
        phone="+34600111222",
        **kw,
    )


# --------------------------------------------------------------------------- #
#  Confirmación
# --------------------------------------------------------------------------- #
async def test_booking_defaults_to_pending(db_session, seed):
    appt = await _book(db_session, seed)
    assert appt.status == AppointmentStatus.PENDING


async def test_booking_can_be_created_confirmed(db_session, seed):
    appt = await _book(db_session, seed, status=AppointmentStatus.CONFIRMED)
    assert appt.status == AppointmentStatus.CONFIRMED


# --------------------------------------------------------------------------- #
#  Snapshot de precio/duración/nombre
# --------------------------------------------------------------------------- #
async def test_snapshot_freezes_price_and_duration(db_session, seed):
    appt = await _book(db_session, seed)
    assert appt.service_name == "Corte"
    assert appt.price == Decimal("12.00")
    assert appt.duration_min == 30

    # Cambia el precio del servicio DESPUÉS de reservar.
    service = await db_session.get(Service, seed.service_ids["Corte"])
    service.price = Decimal("99.00")
    await db_session.commit()

    refreshed = await db_session.get(Appointment, appt.id)
    # La cita conserva el precio aplicado al reservar, no el nuevo.
    assert refreshed.price == Decimal("12.00")


# --------------------------------------------------------------------------- #
#  Endpoint de estado (back-office)
# --------------------------------------------------------------------------- #
async def test_admin_can_mark_status(client, seed):
    start = datetime.combine(next_weekday(0), time(11, 0)).isoformat()
    booking = await client.post(
        f"/admin/businesses/{seed.business_id}/appointments",
        json={
            "service_id": seed.service_ids["Corte"],
            "start_at": start,
            "customer": {"phone": "+34699000123"},
        },
    )
    appt = booking.json()
    assert appt["status"] == "pending"
    assert appt["price"] == "12.00" and appt["duration_min"] == 30

    aid = appt["id"]
    for new_status in ("confirmed", "completed", "no_show"):
        resp = await client.post(
            f"/admin/businesses/{seed.business_id}/appointments/{aid}/status",
            json={"status": new_status},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == new_status


async def test_status_invalid_value_is_422(client, seed):
    start = datetime.combine(next_weekday(0), time(12, 0)).isoformat()
    booking = await client.post(
        f"/admin/businesses/{seed.business_id}/appointments",
        json={
            "service_id": seed.service_ids["Corte"],
            "start_at": start,
            "customer": {"phone": "+34699000124"},
        },
    )
    aid = booking.json()["id"]
    resp = await client.post(
        f"/admin/businesses/{seed.business_id}/appointments/{aid}/status",
        json={"status": "inventado"},
    )
    assert resp.status_code == 422
