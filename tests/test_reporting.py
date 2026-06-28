"""Tests del panel: agenda, clientes y facturación."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal

from app.models import AppointmentStatus
from app.tools import book_appointment
from tests.conftest import next_weekday


async def _book(db_session, seed, *, phone, hour, status, service="Corte"):
    appt = await book_appointment(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids[service],
        start_at=datetime.combine(next_weekday(0), time(hour, 0)),
        phone=phone,
        name="Cliente " + phone[-2:],
        status=status,
    )
    return appt


async def test_agenda_lists_day_with_customer_and_professional(client, seed, db_session):
    day = next_weekday(0)
    await _book(db_session, seed, phone="+34600000011", hour=10, status=AppointmentStatus.CONFIRMED)
    await _book(db_session, seed, phone="+34600000012", hour=11, status=AppointmentStatus.PENDING)
    await db_session.commit()

    resp = await client.get(f"/admin/businesses/{seed.business_id}/agenda?date={day}")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 2
    assert data["resources"]  # profesionales para agrupar
    item = data["items"][0]
    assert item["customer_phone"].startswith("+34")
    assert item["resource_name"]
    assert item["service_name"] == "Corte"
    assert item["price"] == "12.00"


async def test_customers_stats(client, seed, db_session):
    a = await _book(db_session, seed, phone="+34600000020", hour=10, status=AppointmentStatus.COMPLETED)
    await _book(db_session, seed, phone="+34600000020", hour=11, status=AppointmentStatus.NO_SHOW)
    await db_session.commit()

    resp = await client.get(f"/admin/businesses/{seed.business_id}/customers")
    assert resp.status_code == 200
    rows = resp.json()
    cust = next(c for c in rows if c["phone"] == "+34600000020")
    assert cust["total"] == 2
    assert cust["completed"] == 1
    assert cust["no_shows"] == 1
    assert cust["total_spent"] == "12.00"  # solo la completada cuenta
    assert cust["last_visit"] is not None

    # Detalle del cliente con sus citas.
    detail = await client.get(
        f"/admin/businesses/{seed.business_id}/customers/{a.customer_id}"
    )
    assert detail.status_code == 200
    assert len(detail.json()["appointments"]) == 2


async def test_edit_customer_name(client, seed, db_session):
    a = await _book(db_session, seed, phone="+34600000030", hour=12, status=AppointmentStatus.CONFIRMED)
    await db_session.commit()
    resp = await client.patch(
        f"/admin/businesses/{seed.business_id}/customers/{a.customer_id}",
        json={"name": "Renombrado"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renombrado"


async def test_billing_breakdown(client, seed, db_session):
    # 2 completadas (ingreso), 1 no-show (perdido), 1 confirmada futura (previsto).
    await _book(db_session, seed, phone="+34600000040", hour=9, status=AppointmentStatus.COMPLETED)
    await _book(db_session, seed, phone="+34600000041", hour=10, status=AppointmentStatus.COMPLETED, service="Tinte")
    await _book(db_session, seed, phone="+34600000042", hour=11, status=AppointmentStatus.NO_SHOW)
    await _book(db_session, seed, phone="+34600000043", hour=12, status=AppointmentStatus.CONFIRMED)
    await db_session.commit()

    day = next_weekday(0)
    to = day + timedelta(days=1)
    resp = await client.get(
        f"/admin/businesses/{seed.business_id}/billing?date_from={day}&date_to={to}"
    )
    assert resp.status_code == 200
    b = resp.json()
    # Corte 12 + Tinte 40 completados = 52.
    assert Decimal(b["revenue_billed"]) == Decimal("52.00")
    assert Decimal(b["revenue_lost"]) == Decimal("12.00")  # no-show corte
    assert Decimal(b["revenue_expected"]) == Decimal("12.00")  # confirmada futura
    assert b["appointments"] == 4
    services = {x["key"]: x["revenue"] for x in b["by_service"]}
    assert services["Tinte"] == "40.00"
    assert b["by_professional"]  # desglose por profesional
