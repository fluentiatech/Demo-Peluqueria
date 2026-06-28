"""Tests del modelo de datos: constraints e integridad."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import (
    Appointment,
    AppointmentStatus,
    Business,
    Customer,
    Resource,
    Service,
)


async def test_service_persists_duration_and_price(db_session, seed):
    service = await db_session.get(Service, seed.service_ids["Corte"])
    assert service.duration_min == 30
    assert service.price == Decimal("12.00")
    assert service.active is True


async def test_unique_resource_slot_blocks_duplicate(db_session, seed):
    """El UNIQUE(resource_id, start_at) impide dos citas idénticas."""
    start = datetime(2030, 1, 7, 10, 0)
    customer = Customer(business_id=seed.business_id, phone="+34600000000")
    db_session.add(customer)
    await db_session.flush()

    end = start + timedelta(minutes=30)
    common = {
        "business_id": seed.business_id,
        "service_id": seed.service_ids["Corte"],
        "resource_id": seed.resource_ids[0],
        "customer_id": customer.id,
        "start_at": start,
        "end_at": end,
        "block_start_at": start,
        "block_end_at": end,
    }
    db_session.add(Appointment(**common, status=AppointmentStatus.CONFIRMED))
    await db_session.flush()

    db_session.add(Appointment(**common, status=AppointmentStatus.PENDING))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_unique_customer_phone_per_business(db_session, seed):
    db_session.add(Customer(business_id=seed.business_id, phone="+34611111111"))
    await db_session.flush()
    db_session.add(Customer(business_id=seed.business_id, phone="+34611111111"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_cascade_delete_removes_services(db_session, seed):
    business = await db_session.get(Business, seed.business_id)
    await db_session.delete(business)
    await db_session.flush()
    assert await db_session.get(Service, seed.service_ids["Corte"]) is None
    assert await db_session.get(Resource, seed.resource_ids[0]) is None
