"""Tests del cifrado de PII en reposo (Fernet aleatorio + AES-SIV determinista)."""
from __future__ import annotations

from datetime import datetime, time

import pytest
from sqlalchemy import select, text

from app import crypto
from app.config import settings
from app.models import Customer
from app.tools import book_appointment
from tests.conftest import next_weekday

MASTER = "clave-maestra-de-prueba-rota-esto-en-prod"


@pytest.fixture
def pii_key(monkeypatch):
    crypto._cache.clear()
    monkeypatch.setattr(settings, "pii_encryption_key", MASTER)
    yield
    crypto._cache.clear()


def test_passthrough_without_key(monkeypatch):
    crypto._cache.clear()
    monkeypatch.setattr(settings, "pii_encryption_key", "")
    assert crypto.encrypt("Ana") == "Ana"
    assert crypto.decrypt("Ana") == "Ana"
    assert crypto.det_encrypt("+34600111222") == "+34600111222"
    assert crypto.is_enabled() is False


def test_randomized_roundtrip(pii_key):
    t1 = crypto.encrypt("Ana López")
    t2 = crypto.encrypt("Ana López")
    assert t1.startswith("enc:") and "Ana" not in t1
    assert t1 != t2  # IV aleatorio → cifrados distintos
    assert crypto.decrypt(t1) == "Ana López"
    assert crypto.decrypt(t2) == "Ana López"


def test_deterministic_is_searchable(pii_key):
    a = crypto.det_encrypt("+34600111222")
    b = crypto.det_encrypt("+34600111222")
    assert a == b  # determinista → permite buscar por igualdad y UNIQUE
    assert a.startswith("det:") and "+34600" not in a
    assert crypto.det_decrypt(a) == "+34600111222"


def test_legacy_plaintext_tolerated(pii_key):
    # Datos previos al cifrado (sin prefijo) se devuelven tal cual.
    assert crypto.decrypt("nombre-en-claro") == "nombre-en-claro"
    assert crypto.det_decrypt("+34600999000") == "+34600999000"


async def test_customer_pii_encrypted_at_rest(db_session, seed, pii_key):
    start = datetime.combine(next_weekday(0), time(10, 0))
    appt = await book_appointment(
        db_session,
        business_id=seed.business_id,
        service_id=seed.service_ids["Corte"],
        start_at=start,
        phone="+34600999888",
        name="Carla Ruiz",
        notes="alérgica al amoníaco",
    )
    await db_session.commit()

    # La búsqueda por teléfono sigue funcionando (cifrado determinista).
    found = await db_session.scalar(
        select(Customer).where(
            Customer.business_id == seed.business_id,
            Customer.phone == "+34600999888",
        )
    )
    assert found is not None
    assert found.name == "Carla Ruiz"  # se descifra de forma transparente

    # En la BD los valores están cifrados (no en claro).
    raw = (
        await db_session.execute(
            text("SELECT phone, name FROM customers WHERE id = :i"), {"i": found.id}
        )
    ).first()
    assert raw.phone.startswith("det:") and "+34600999888" not in raw.phone
    assert raw.name.startswith("enc:") and "Carla" not in raw.name

    raw_notes = (
        await db_session.execute(
            text("SELECT notes FROM appointments WHERE id = :i"), {"i": appt.id}
        )
    ).scalar()
    assert raw_notes.startswith("enc:") and "amoníaco" not in raw_notes
