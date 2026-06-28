"""Tests del login del panel: cookie de sesión, CSRF, bloqueo por fallos, TOTP."""
from __future__ import annotations

import time

import pytest

from app import totp
from app.config import settings

KEY = "clave-admin-de-prueba-1234567890"


@pytest.fixture
def admin_key(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_key", KEY)
    yield KEY


async def test_login_sets_cookie_and_allows_access(client, admin_key):
    r = await client.post("/admin/session", json={"api_key": KEY})
    assert r.status_code == 200
    assert "sid" in r.cookies or "sid" in client.cookies
    csrf = r.json()["csrf"]
    assert csrf

    # Con la cookie, un GET protegido funciona sin enviar la API key.
    g = await client.get("/admin/businesses")
    assert g.status_code == 200


async def test_login_wrong_key_is_rejected(client, admin_key):
    r = await client.post("/admin/session", json={"api_key": "incorrecta"})
    assert r.status_code == 401


async def test_mutation_requires_csrf_with_cookie(client, admin_key):
    csrf = (await client.post("/admin/session", json={"api_key": KEY})).json()["csrf"]

    # Sin token CSRF, una escritura se rechaza (403) aunque la cookie sea válida.
    bad = await client.post("/admin/businesses", json={"name": "Sin CSRF"})
    assert bad.status_code == 403

    ok = await client.post(
        "/admin/businesses", json={"name": "Con CSRF"},
        headers={"X-CSRF-Token": csrf},
    )
    assert ok.status_code == 201


async def test_lockout_after_repeated_failures(client, admin_key, monkeypatch):
    monkeypatch.setattr(settings, "login_max_attempts", 3)
    for _ in range(3):
        assert (await client.post("/admin/session", json={"api_key": "mal"})).status_code == 401
    # El siguiente intento (aun con la clave correcta) queda bloqueado.
    locked = await client.post("/admin/session", json={"api_key": KEY})
    assert locked.status_code == 429


async def test_api_key_header_still_works(client, admin_key):
    """Compatibilidad: los clientes no-navegador siguen usando X-API-Key."""
    r = await client.get("/admin/businesses", headers={"X-API-Key": KEY})
    assert r.status_code == 200


async def test_logout_clears_session(client, admin_key):
    await client.post("/admin/session", json={"api_key": KEY})
    assert (await client.get("/admin/businesses")).status_code == 200
    await client.delete("/admin/session")
    client.cookies.clear()
    # Sin cookie ni clave → 401.
    assert (await client.get("/admin/businesses")).status_code == 401


async def test_totp_required_when_configured(client, admin_key, monkeypatch):
    secret = "JBSWY3DPEHPK3PXP"
    monkeypatch.setattr(settings, "admin_totp_secret", secret)

    # Sin código → rechazado.
    assert (await client.post("/admin/session", json={"api_key": KEY})).status_code == 401

    code = totp._code_at(totp._b32_decode(secret), int(time.time() // 30))
    ok = await client.post("/admin/session", json={"api_key": KEY, "totp": code})
    assert ok.status_code == 200


def test_totp_verifies_own_code():
    secret = "JBSWY3DPEHPK3PXP"
    now = time.time()
    code = totp._code_at(totp._b32_decode(secret), int(now // 30))
    assert totp.verify(secret, code, at=now) is True
    assert totp.verify(secret, "000000", at=now) is False
