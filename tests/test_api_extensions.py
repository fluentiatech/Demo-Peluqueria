"""Tests de integración de los endpoints nuevos: capacidad, cierres y ausencias."""
from __future__ import annotations

from datetime import datetime, time

from tests.conftest import next_weekday


async def test_capacity_assignment_endpoint(client, seed):
    bid, sid = seed.business_id, seed.service_ids["Corte"]

    # Inicialmente sin restricción.
    assert (await client.get(f"/admin/businesses/{bid}/services/{sid}/resources")).json() == []

    put = await client.put(
        f"/admin/businesses/{bid}/services/{sid}/resources",
        json={"resource_ids": [seed.resource_ids[0]]},
    )
    assert put.status_code == 200
    assert put.json() == [seed.resource_ids[0]]

    got = await client.get(f"/admin/businesses/{bid}/services/{sid}/resources")
    assert got.json() == [seed.resource_ids[0]]


async def test_capacity_rejects_foreign_resource(client, seed):
    bid, sid = seed.business_id, seed.service_ids["Corte"]
    resp = await client.put(
        f"/admin/businesses/{bid}/services/{sid}/resources",
        json={"resource_ids": ["recurso-inexistente"]},
    )
    assert resp.status_code == 400


async def test_closure_crud_and_effect(client, seed):
    bid, sid = seed.business_id, seed.service_ids["Corte"]
    day = next_weekday(2)

    created = await client.post(
        f"/admin/businesses/{bid}/closures",
        json={"date": str(day), "is_closed": True, "reason": "Festivo"},
    )
    assert created.status_code == 201
    closure_id = created.json()["id"]

    listed = await client.get(f"/admin/businesses/{bid}/closures")
    assert len(listed.json()) == 1

    # Ese día no hay disponibilidad.
    avail = await client.post(
        f"/admin/businesses/{bid}/availability",
        json={"service_id": sid, "date_from": str(day), "date_to": str(day)},
    )
    assert avail.json() == []

    deleted = await client.delete(f"/admin/businesses/{bid}/closures/{closure_id}")
    assert deleted.status_code == 204
    assert (await client.get(f"/admin/businesses/{bid}/closures")).json() == []


async def test_time_off_endpoint_blocks_booking(client, seed):
    bid, sid = seed.business_id, seed.service_ids["Corte"]
    day = next_weekday(0)

    off = await client.post(
        f"/admin/businesses/{bid}/time-off",
        json={
            "resource_id": seed.resource_ids[0],
            "start_at": datetime.combine(day, time(9, 0)).isoformat(),
            "end_at": datetime.combine(day, time(14, 0)).isoformat(),
            "reason": "Vacaciones",
        },
    )
    assert off.status_code == 201

    listed = await client.get(f"/admin/businesses/{bid}/time-off")
    assert len(listed.json()) == 1

    # El recurso 0 está ausente todo el día; reservar en él falla.
    resp = await client.post(
        f"/admin/businesses/{bid}/appointments",
        json={
            "service_id": sid,
            "start_at": datetime.combine(day, time(10, 0)).isoformat(),
            "customer": {"phone": "+34699111222"},
            "resource_id": seed.resource_ids[0],
        },
    )
    assert resp.status_code == 409


async def test_time_off_invalid_range_is_422(client, seed):
    bid = seed.business_id
    day = next_weekday(0)
    resp = await client.post(
        f"/admin/businesses/{bid}/time-off",
        json={
            "resource_id": seed.resource_ids[0],
            "start_at": datetime.combine(day, time(12, 0)).isoformat(),
            "end_at": datetime.combine(day, time(10, 0)).isoformat(),
        },
    )
    assert resp.status_code == 422
