"""Tests de la capa de auditoría: cadena de hashes, manipulación, alertas, API."""
from __future__ import annotations

from sqlalchemy import func, select

from app import audit
from app.models import AuditLog, EventLog


async def test_record_chains_and_verifies(db_session):
    await audit.record(
        db_session, action="mutation", method="POST", path="/admin/a",
        status=201, actor_ip="1.2.3.4", actor_key_fp="abc123",
    )
    await audit.record(
        db_session, action="mutation", method="DELETE", path="/admin/b", status=204
    )
    await db_session.commit()

    res = await audit.verify_chain(db_session)
    assert res["ok"] is True
    assert res["count"] == 2

    rows = (await db_session.scalars(select(AuditLog).order_by(AuditLog.seq))).all()
    assert [r.seq for r in rows] == [1, 2]
    # Cada fila encadena con el hash de la anterior.
    assert rows[1].prev_hash == rows[0].hash


async def test_tampering_breaks_the_chain(db_session):
    await audit.record(db_session, action="mutation", method="POST", path="/a", status=201)
    await audit.record(db_session, action="mutation", method="POST", path="/b", status=201)
    await db_session.commit()

    # Un atacante altera un registro pasado en la BD.
    first = (await db_session.scalars(select(AuditLog).order_by(AuditLog.seq))).first()
    first.path = "/manipulado"
    await db_session.commit()

    res = await audit.verify_chain(db_session)
    assert res["ok"] is False
    assert res["broken_seq"] == 1


async def test_scan_security_alerts_over_threshold(db_session, seed, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "security_alert_threshold", 3)
    for _ in range(3):
        await audit.record(
            db_session, action="security", method="GET", path="/admin/x",
            status=401, business_id=seed.business_id,
        )
    await db_session.commit()

    assert await audit.scan_security(db_session) == 1
    n = await db_session.scalar(
        select(func.count()).select_from(EventLog).where(EventLog.type == "error")
    )
    assert n == 1
    # Idempotente: con una alerta ya pendiente no se duplica.
    assert await audit.scan_security(db_session) == 0


async def test_scan_security_silent_under_threshold(db_session, seed, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "security_alert_threshold", 10)
    await audit.record(
        db_session, action="security", method="GET", path="/x",
        status=429, business_id=seed.business_id,
    )
    await db_session.commit()
    assert await audit.scan_security(db_session) == 0


async def test_middleware_audits_admin_mutation(client, db_session):
    r = await client.post("/admin/businesses", json={"name": "Auditada SL"})
    assert r.status_code == 201

    rows = (await db_session.scalars(select(AuditLog))).all()
    assert any(
        a.action == "mutation" and a.method == "POST" and a.path == "/admin/businesses"
        for a in rows
    )


async def test_audit_action_filter_rejects_unknown_value(client):
    """Defensa en profundidad: el filtro `action` solo acepta valores conocidos."""
    # Aunque la consulta va parametrizada (no inyectable), un valor fuera del
    # conjunto permitido se rechaza con 422 antes de tocar la BD.
    r = await client.get("/admin/audit?action=' OR '1'='1")
    assert r.status_code == 422
    ok = await client.get("/admin/audit?action=security")
    assert ok.status_code == 200


async def test_audit_api_list_and_verify(client):
    await client.post("/admin/businesses", json={"name": "Otra SL"})
    listed = (await client.get("/admin/audit")).json()
    assert any(a["action"] == "mutation" for a in listed)

    verify = (await client.get("/admin/audit/verify")).json()
    assert verify["ok"] is True
    assert verify["count"] >= 1
