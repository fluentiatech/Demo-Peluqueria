"""Tests del webhook de WhatsApp: verificación de token y firma."""
from __future__ import annotations

import hashlib
import hmac

from app.config import settings


async def test_verify_handshake_ok(client):
    resp = await client.get(
        "/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": settings.whatsapp_verify_token,
            "hub.challenge": "12345",
        },
    )
    assert resp.status_code == 200
    assert resp.text == "12345"


async def test_verify_handshake_wrong_token(client):
    resp = await client.get(
        "/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "token-incorrecto",
            "hub.challenge": "12345",
        },
    )
    assert resp.status_code == 403


async def test_receive_without_secret_accepts(client):
    # Sin app_secret configurado (caso desarrollo) se acepta el evento.
    resp = await client.post("/webhook", json={"object": "whatsapp_business_account"})
    assert resp.status_code == 200


async def test_receive_with_bad_signature_is_rejected(client, monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_app_secret", "secreto")
    resp = await client.post(
        "/webhook",
        content=b"{}",
        headers={"X-Hub-Signature-256": "sha256=deadbeef"},
    )
    assert resp.status_code == 403


async def test_receive_with_valid_signature_accepts(client, monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_app_secret", "secreto")
    body = b'{"object":"whatsapp_business_account"}'
    sig = hmac.new(b"secreto", body, hashlib.sha256).hexdigest()
    resp = await client.post(
        "/webhook",
        content=body,
        headers={"X-Hub-Signature-256": f"sha256={sig}"},
    )
    assert resp.status_code == 200
