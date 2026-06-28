"""Tests de los endurecimientos de seguridad."""
from __future__ import annotations

from datetime import datetime, time, timedelta

from app import security
from app.config import settings
from app.integrations.whatsapp import send_text
from app.tools import book_appointment
from tests.conftest import next_weekday


# --------------------------------------------------------------------------- #
#  Autenticación de la API admin / agente
# --------------------------------------------------------------------------- #
async def test_admin_requires_api_key_when_configured(client, seed, monkeypatch):
    monkeypatch.setattr(settings, "admin_api_key", "secreta")

    # Sin cabecera → 401.
    assert (await client.get("/admin/businesses")).status_code == 401
    # Con clave incorrecta → 401.
    bad = await client.get("/admin/businesses", headers={"X-API-Key": "mala"})
    assert bad.status_code == 401
    # Con clave correcta → 200.
    ok = await client.get("/admin/businesses", headers={"X-API-Key": "secreta"})
    assert ok.status_code == 200


async def test_admin_fails_closed_in_production_without_key(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_api_key", "")
    monkeypatch.setattr(settings, "app_env", "production")
    resp = await client.get("/admin/businesses")
    assert resp.status_code == 503


async def test_multiple_api_keys_for_rotation(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_api_key", "alpha-key, beta-key")
    assert (await client.get("/admin/businesses", headers={"X-API-Key": "alpha-key"})).status_code == 200
    assert (await client.get("/admin/businesses", headers={"X-API-Key": "beta-key"})).status_code == 200
    assert (await client.get("/admin/businesses", headers={"X-API-Key": "gamma-key"})).status_code == 401


async def test_api_key_hash_auth(client, monkeypatch):
    """La config solo guarda el HASH; el cliente envía la clave en claro."""
    from app.security import hash_key

    key = "clave-en-claro-de-prueba-12345"
    monkeypatch.setattr(settings, "admin_api_key", "")  # sin claves en claro
    monkeypatch.setattr(settings, "admin_api_key_hashes", hash_key(key))

    assert (await client.get("/admin/businesses", headers={"X-API-Key": key})).status_code == 200
    bad = await client.get("/admin/businesses", headers={"X-API-Key": "otra-clave"})
    assert bad.status_code == 401


def test_hash_key_is_sha256():
    import hashlib

    from app.security import hash_key

    assert hash_key("abc") == hashlib.sha256(b"abc").hexdigest()


async def test_short_key_rejected_in_production(client, monkeypatch):
    # En producción una clave corta no cuenta como válida → fail-closed (503).
    monkeypatch.setattr(settings, "admin_api_key", "corta")
    monkeypatch.setattr(settings, "app_env", "production")
    resp = await client.get("/admin/businesses", headers={"X-API-Key": "corta"})
    assert resp.status_code == 503


async def test_trusted_network_bypasses_api_key(client, monkeypatch):
    """Desde una red de confianza (LAN/VPN) se entra al admin sin API key."""
    monkeypatch.setattr(settings, "admin_api_key", "clave-larga-de-verdad-1234")
    monkeypatch.setattr(settings, "trusted_admin_cidrs", "192.168.0.0/16")
    monkeypatch.setattr(security, "client_ip", lambda _r: "192.168.1.50")
    # Sin cabecera de clave, pero IP de confianza → 200.
    assert (await client.get("/admin/businesses")).status_code == 200


async def test_untrusted_network_still_needs_key(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_api_key", "clave-larga-de-verdad-1234")
    monkeypatch.setattr(settings, "trusted_admin_cidrs", "192.168.0.0/16")
    monkeypatch.setattr(security, "client_ip", lambda _r: "8.8.8.8")
    assert (await client.get("/admin/businesses")).status_code == 401


async def test_admin_bruteforce_is_throttled(client, monkeypatch):
    """El rate-limit corre ANTES de la auth: frena la fuerza bruta de la API key."""
    monkeypatch.setattr(settings, "admin_api_key", "una-clave-larga-de-verdad-1234")
    monkeypatch.setattr(settings, "rate_limit_per_min", 2)
    s1 = (await client.get("/admin/businesses")).status_code  # sin key → 401
    s2 = (await client.get("/admin/businesses")).status_code
    s3 = (await client.get("/admin/businesses")).status_code
    assert s1 == 401 and s2 == 401 and s3 == 429


# --------------------------------------------------------------------------- #
#  Cabeceras de seguridad
# --------------------------------------------------------------------------- #
async def test_extended_security_headers(client):
    h = (await client.get("/health")).headers
    assert h["cross-origin-opener-policy"] == "same-origin"
    assert h["cross-origin-resource-policy"] == "same-origin"
    assert "permissions-policy" in h
    # En desarrollo (http) no se emite HSTS.
    assert "strict-transport-security" not in h


# --------------------------------------------------------------------------- #
#  Dashboard: CSP con nonce y sin CDN externo
# --------------------------------------------------------------------------- #
async def test_dashboard_csp_and_no_cdn(client):
    resp = await client.get("/dashboard")
    assert resp.status_code == 200
    csp = resp.headers.get("content-security-policy", "")
    assert "script-src 'nonce-" in csp
    assert "default-src 'self'" in csp
    # Sin dependencias de CDN (riesgo de cadena de suministro).
    assert "jsdelivr" not in resp.text and "cdn." not in resp.text
    assert "<script src" not in resp.text  # nada de scripts externos
    # El nonce de la CSP coincide con el de las etiquetas.
    nonce = csp.split("script-src 'nonce-")[1].split("'")[0]
    assert f'nonce="{nonce}"' in resp.text


async def test_panel_strict_csp(client):
    """El panel sirve con CSP estricta: scripts solo propios, sin CDN externo."""
    resp = await client.get("/panel/agenda.html")
    assert resp.status_code == 200
    csp = resp.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp  # sin 'unsafe-inline' para scripts → corta XSS
    assert "frame-ancestors 'none'" in csp


def test_dashboard_on_property(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "dashboard_enabled", None)
    assert settings.dashboard_on is False
    monkeypatch.setattr(settings, "dashboard_enabled", True)
    assert settings.dashboard_on is True
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "dashboard_enabled", None)
    assert settings.dashboard_on is True


# --------------------------------------------------------------------------- #
#  Webhook: firma obligatoria en producción
# --------------------------------------------------------------------------- #
async def test_webhook_fails_closed_in_production_without_signature(client, monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "whatsapp_app_secret", "")
    resp = await client.post("/webhook", json={"object": "x"})
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
#  Rate limiting
# --------------------------------------------------------------------------- #
async def test_rate_limit_returns_429(client, seed, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_min", 2)
    url = f"/admin/businesses/{seed.business_id}/ask"
    body = {"message": "hola"}
    assert (await client.post(url, json=body)).status_code == 200
    assert (await client.post(url, json=body)).status_code == 200
    assert (await client.post(url, json=body)).status_code == 429


async def test_rate_limit_redis_falls_back_to_memory(client, seed, monkeypatch):
    """Con backend Redis caído, el limitador sigue funcionando vía memoria."""
    monkeypatch.setattr(settings, "rate_limit_backend", "redis")
    monkeypatch.setattr(settings, "rate_limit_per_min", 2)

    def boom() -> object:
        raise RuntimeError("redis caído")

    monkeypatch.setattr(security, "_get_redis", boom)

    url = f"/admin/businesses/{seed.business_id}/ask"
    body = {"message": "hola"}
    assert (await client.post(url, json=body)).status_code == 200
    assert (await client.post(url, json=body)).status_code == 200
    assert (await client.post(url, json=body)).status_code == 429


# --------------------------------------------------------------------------- #
#  Validación de entrada (DoS / robustez)
# --------------------------------------------------------------------------- #
async def test_availability_range_is_bounded(client, seed):
    sid = seed.service_ids["Corte"]
    today = next_weekday(0)
    far = today + timedelta(days=settings.availability_max_days + 5)
    resp = await client.post(
        f"/admin/businesses/{seed.business_id}/availability",
        json={"service_id": sid, "date_from": str(today), "date_to": str(far)},
    )
    assert resp.status_code == 422


async def test_invalid_opening_hours_rejected(client):
    resp = await client.post(
        "/admin/businesses",
        json={"name": "X", "opening_hours": {"0": [["9", "14:00"]]}},
    )
    assert resp.status_code == 422


async def test_invalid_weekday_rejected(client):
    resp = await client.post(
        "/admin/businesses",
        json={"name": "X", "opening_hours": {"9": [["09:00", "14:00"]]}},
    )
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
#  SSRF / inyección en la ruta de Graph
# --------------------------------------------------------------------------- #
async def test_send_text_rejects_non_numeric_ids(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_access_token", "TOKEN")
    assert await send_text("34600111222", "hola", "../../evil") is False
    assert await send_text("no-numerico", "hola", "123456") is False


# --------------------------------------------------------------------------- #
#  Endurecimiento HTTP (cabeceras, tamaño de cuerpo) y validación E.164
# --------------------------------------------------------------------------- #
async def test_security_headers_present(client):
    resp = await client.get("/health")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"


async def test_body_too_large_is_413(client, monkeypatch):
    monkeypatch.setattr(settings, "max_body_bytes", 10)
    resp = await client.post(
        "/admin/businesses", json={"name": "un negocio con nombre largo"}
    )
    assert resp.status_code == 413


async def test_invalid_phone_rejected(client, seed):
    from datetime import datetime, time

    resp = await client.post(
        f"/admin/businesses/{seed.business_id}/appointments",
        json={
            "service_id": seed.service_ids["Corte"],
            "start_at": datetime.combine(next_weekday(0), time(10, 0)).isoformat(),
            "customer": {"phone": "no-es-telefono"},
        },
    )
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
#  RGPD: consentimiento registrado al crear cliente
# --------------------------------------------------------------------------- #
async def test_consent_recorded_on_customer_creation(db_session, seed):
    appt = await book_appointment(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        start_at=datetime.combine(next_weekday(0), time(10, 0)),
        phone="+34600555444",
        name="Marta",
    )
    from app.models import Customer

    customer = await db_session.get(Customer, appt.customer_id)
    assert customer.consent_at is not None
