"""Tests de integración de la API admin a través del cliente HTTP."""
from __future__ import annotations

from datetime import datetime, time

from tests.conftest import next_weekday


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_service_crud_flow(client):
    biz = (await client.post("/admin/businesses", json={"name": "Barbería X"})).json()
    bid = biz["id"]

    created = await client.post(
        f"/admin/businesses/{bid}/services",
        json={"name": "Afeitado", "duration_min": 20, "price": "9.50"},
    )
    assert created.status_code == 201
    sid = created.json()["id"]

    listed = (await client.get(f"/admin/businesses/{bid}/services")).json()
    assert len(listed) == 1
    assert listed[0]["duration_min"] == 20

    patched = await client.patch(
        f"/admin/businesses/{bid}/services/{sid}", json={"price": "11.00"}
    )
    assert patched.json()["price"] == "11.00"

    # Borrado lógico: desaparece del listado por defecto.
    await client.delete(f"/admin/businesses/{bid}/services/{sid}")
    assert (await client.get(f"/admin/businesses/{bid}/services")).json() == []


async def test_pricing_endpoint(client, seed):
    resp = await client.get(f"/admin/businesses/{seed.business_id}/pricing")
    assert resp.status_code == 200
    names = {item["name"] for item in resp.json()}
    assert names == {"Corte", "Tinte"}


async def test_booking_flow_and_double_booking(client, seed):
    bid = seed.business_id
    sid = seed.service_ids["Corte"]
    start = datetime.combine(next_weekday(0), time(11, 0)).isoformat()

    # Disponibilidad.
    avail = await client.post(
        f"/admin/businesses/{bid}/availability",
        json={"service_id": sid, "date_from": str(next_weekday(0)), "date_to": str(next_weekday(0))},
    )
    assert avail.status_code == 200
    assert len(avail.json()) > 0

    # Reserva.
    booking = await client.post(
        f"/admin/businesses/{bid}/appointments",
        json={
            "service_id": sid,
            "start_at": start,
            "customer": {"phone": "+34699000001", "name": "Lola"},
            "resource_id": seed.resource_ids[0],
            "idempotency_key": "wamid.X1",
        },
    )
    assert booking.status_code == 201
    appt_id = booking.json()["id"]

    # Idempotencia: misma key → misma cita.
    again = await client.post(
        f"/admin/businesses/{bid}/appointments",
        json={
            "service_id": sid,
            "start_at": start,
            "customer": {"phone": "+34699000001"},
            "idempotency_key": "wamid.X1",
        },
    )
    assert again.json()["id"] == appt_id

    # Doble reserva mismo recurso → 409.
    clash = await client.post(
        f"/admin/businesses/{bid}/appointments",
        json={
            "service_id": sid,
            "start_at": start,
            "customer": {"phone": "+34699000002"},
            "resource_id": seed.resource_ids[0],
            "idempotency_key": "wamid.X2",
        },
    )
    assert clash.status_code == 409

    # Cancelación.
    cancel = await client.post(
        f"/admin/businesses/{bid}/appointments/{appt_id}/cancel"
    )
    assert cancel.status_code == 200
    assert cancel.json()["cancelled"] is True


async def test_service_on_missing_business_is_404(client):
    # UUID válido pero inexistente → 404 (no encontrado).
    resp = await client.post(
        "/admin/businesses/00000000-0000-4000-8000-000000000000/services",
        json={"name": "X", "duration_min": 10, "price": "1.00"},
    )
    assert resp.status_code == 404


async def test_malformed_business_id_is_422(client):
    # Identificador con formato no-UUID → 422 (validación, antes de tocar la BD).
    resp = await client.post(
        "/admin/businesses/no-existe/services",
        json={"name": "X", "duration_min": 10, "price": "1.00"},
    )
    assert resp.status_code == 422
