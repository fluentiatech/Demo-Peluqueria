"""Validación de entrada (defensa en profundidad sobre la parametrización SQL).

La inyección SQL ya está cerrada por el ORM; estos tests fijan que, además, los
identificadores y el texto libre se validan antes de llegar a la BD.
"""
from __future__ import annotations

from datetime import datetime, time

from tests.conftest import next_weekday

GOOD_UUID = "00000000-0000-4000-8000-000000000000"


async def test_path_id_must_be_uuid(client, seed):
    # Id con formato no-UUID → 422 en cualquier endpoint admin con *_id de ruta.
    bad = await client.get("/admin/businesses/' OR '1'='1")
    assert bad.status_code == 422
    # Id UUID válido del seed → 200.
    ok = await client.get(f"/admin/businesses/{seed.business_id}")
    assert ok.status_code == 200


async def test_appointment_id_uuid_validated(client, seed):
    r = await client.post(
        f"/admin/businesses/{seed.business_id}/appointments/not-a-uuid/cancel"
    )
    assert r.status_code == 422


async def test_customer_name_rejects_control_chars(client, seed):
    start = datetime.combine(next_weekday(0), time(10, 0)).isoformat()
    r = await client.post(
        f"/admin/businesses/{seed.business_id}/appointments",
        json={
            "service_id": seed.service_ids["Corte"],
            "start_at": start,
            "customer": {"phone": "+34600111222", "name": "Ana\x00Pérez"},
        },
    )
    assert r.status_code == 422


async def test_notes_length_bounded(client, seed):
    start = datetime.combine(next_weekday(0), time(10, 0)).isoformat()
    r = await client.post(
        f"/admin/businesses/{seed.business_id}/appointments",
        json={
            "service_id": seed.service_ids["Corte"],
            "start_at": start,
            "customer": {"phone": "+34600111222"},
            "notes": "x" * 1001,
        },
    )
    assert r.status_code == 422
