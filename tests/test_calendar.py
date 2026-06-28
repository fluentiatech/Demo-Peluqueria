"""Tests del calendario del panel: edición de horario y comprobación por día.

El seed abre lunes(0)–sábado(5) de 09:00 a 14:00 (tramo único = horario continuo)
y cierra el domingo(6).
"""
from __future__ import annotations

from tests.conftest import next_weekday


async def test_day_info_open_continuous(client, seed):
    bid = seed.business_id
    monday = next_weekday(0).isoformat()
    info = (await client.get(f"/admin/businesses/{bid}/day-info?date={monday}")).json()
    assert info["is_open"] is True
    assert info["kind"] == "continuo"
    assert info["intervals"] == [["09:00", "14:00"]]
    assert info["is_special"] is False


async def test_day_info_closed_day(client, seed):
    bid = seed.business_id
    sunday = next_weekday(6).isoformat()
    info = (await client.get(f"/admin/businesses/{bid}/day-info?date={sunday}")).json()
    assert info["is_open"] is False
    assert info["kind"] == "cerrado"
    assert info["intervals"] == []


async def test_patch_split_schedule_makes_day_partido(client, seed):
    bid = seed.business_id
    # Horario partido el lunes: mañana y tarde.
    patch = {"opening_hours": {"0": [["09:00", "14:00"], ["16:00", "20:00"]]}}
    resp = await client.patch(f"/admin/businesses/{bid}", json=patch)
    assert resp.status_code == 200
    assert resp.json()["opening_hours"]["0"] == [["09:00", "14:00"], ["16:00", "20:00"]]

    monday = next_weekday(0).isoformat()
    info = (await client.get(f"/admin/businesses/{bid}/day-info?date={monday}")).json()
    assert info["kind"] == "partido"
    assert info["intervals"] == [["09:00", "14:00"], ["16:00", "20:00"]]


async def test_patch_invalid_hours_rejected(client, seed):
    bid = seed.business_id
    # Inicio posterior al fin -> 422 de validación.
    resp = await client.patch(
        f"/admin/businesses/{bid}", json={"opening_hours": {"0": [["14:00", "09:00"]]}}
    )
    assert resp.status_code == 422


async def test_closure_marks_day_special_and_closed(client, seed):
    bid = seed.business_id
    monday = next_weekday(0).isoformat()
    # Cierre puntual un lunes (que normalmente abre).
    created = await client.post(
        f"/admin/businesses/{bid}/closures",
        json={"date": monday, "is_closed": True, "reason": "Festivo local"},
    )
    assert created.status_code == 201

    info = (await client.get(f"/admin/businesses/{bid}/day-info?date={monday}")).json()
    assert info["is_open"] is False
    assert info["is_special"] is True
    assert info["reason"] == "Festivo local"


async def test_closure_special_hours_override_weekly(client, seed):
    bid = seed.business_id
    monday = next_weekday(0).isoformat()
    await client.post(
        f"/admin/businesses/{bid}/closures",
        json={
            "date": monday,
            "is_closed": False,
            "custom_hours": [["10:00", "13:00"]],
            "reason": "Horario reducido",
        },
    )
    info = (await client.get(f"/admin/businesses/{bid}/day-info?date={monday}")).json()
    assert info["is_open"] is True
    assert info["is_special"] is True
    assert info["intervals"] == [["10:00", "13:00"]]
    assert info["kind"] == "continuo"


async def test_get_single_business(client, seed):
    resp = await client.get(f"/admin/businesses/{seed.business_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == seed.business_id
    assert "opening_hours" in resp.json()
