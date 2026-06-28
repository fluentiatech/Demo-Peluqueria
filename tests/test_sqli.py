"""Anti SQL injection: cabeceras y parámetros con payloads no rompen ni filtran.

El acceso a BD es 100% ORM parametrizado, así que un payload viaja como literal.
Estos tests lo demuestran y verifican que un error de BD nunca revela detalles.
"""
from __future__ import annotations

import json

from sqlalchemy.exc import OperationalError, SQLAlchemyError

from app.config import settings

SQLI = "' OR '1'='1'; DROP TABLE businesses;--"


# --------------------------------------------------------------------------- #
#  Cabeceras con payload de inyección
# --------------------------------------------------------------------------- #
async def test_sqli_in_headers_is_harmless(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_api_key", "clave-larga-de-verdad-1234")
    monkeypatch.setattr(settings, "trust_proxy", True)

    resp = await client.get(
        "/admin/businesses",
        headers={"X-API-Key": SQLI, "X-Forwarded-For": SQLI},
    )
    # Clave inválida → 401 limpio, nunca 500 ni traza/SQL en el cuerpo.
    assert resp.status_code == 401
    assert "DROP" not in resp.text and "SQL" not in resp.text

    # La tabla sigue intacta: una consulta válida funciona.
    ok = await client.get(
        "/admin/businesses", headers={"X-API-Key": "clave-larga-de-verdad-1234"}
    )
    assert ok.status_code == 200


# --------------------------------------------------------------------------- #
#  Parámetros (query y path) con payload de inyección
# --------------------------------------------------------------------------- #
async def test_sqli_in_query_param_is_parameterized(client, seed):
    # business_id con payload: se trata como literal → métricas vacías, sin error.
    resp = await client.get(f"/admin/metrics/summary?business_id={SQLI}&days=30")
    assert resp.status_code == 200
    assert resp.json()["llm_calls"] == 0


async def test_sqli_in_path_param_is_rejected(client):
    # Defensa en profundidad: un id de ruta con payload no es UUID → 422 (se corta
    # antes de la BD). La parametrización sigue cubierta por el test de query param.
    resp = await client.get(f"/admin/businesses/{SQLI}/services")
    assert resp.status_code == 422
    assert "DROP" not in resp.text and "SQL" not in resp.text


# --------------------------------------------------------------------------- #
#  Un error de BD nunca revela esquema / consulta / driver
# --------------------------------------------------------------------------- #
async def test_db_error_is_masked():
    from app.main import _db_error

    exc = OperationalError("SELECT secreto FROM clientes", {}, Exception("x"))
    resp = await _db_error(None, exc)
    assert resp.status_code == 500
    body = json.loads(bytes(resp.body))
    assert body == {"detail": "Error interno"}
    assert b"secreto" not in bytes(resp.body)
    assert b"clientes" not in bytes(resp.body)


async def test_unhandled_error_is_masked():
    from app.main import _unhandled

    resp = await _unhandled(None, ValueError("detalle interno sensible"))
    assert resp.status_code == 500
    assert json.loads(bytes(resp.body)) == {"detail": "Error interno"}


def test_sqlalchemy_handler_is_registered():
    from app.main import app

    assert SQLAlchemyError in app.exception_handlers
    assert Exception in app.exception_handlers
