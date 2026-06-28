"""Cifrado de PII en reposo (defensa en profundidad sobre el cifrado de disco).

Dos modos, según el campo:
  - `encrypt`/`decrypt` (Fernet, IV aleatorio): para datos que solo se muestran
    (nombre, notas). Máxima protección: dos cifrados del mismo texto difieren.
  - `det_encrypt`/`det_decrypt` (AES-SIV, determinista): para el teléfono, que se
    usa como clave de búsqueda. El mismo teléfono produce el mismo cifrado, así
    que las consultas por igualdad y el UNIQUE siguen funcionando SIN reescribir
    queries (cifrado + "blind index" en uno).

Sin `PII_ENCRYPTION_KEY` configurada, todo es passthrough (texto claro): el
proyecto arranca y los tests corren igual. El descifrado tolera texto legado en
claro, para permitir una migración gradual con `scripts/encrypt_pii.py`.

La clave maestra deriva por separado el material de Fernet y de AES-SIV, de modo
que una sola variable de entorno basta.
"""
from __future__ import annotations

import base64
import binascii
import hashlib

from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.ciphers.aead import AESSIV

from app.config import settings

_ENC_PREFIX = "enc:"   # Fernet (aleatorio)
_DET_PREFIX = "det:"   # AES-SIV (determinista)

# Cache de cifradores por clave maestra (evita rederivar en cada operación).
_cache: dict[str, tuple[Fernet, AESSIV]] = {}


def _build(master: str) -> tuple[Fernet, AESSIV]:
    # Material independiente para cada esquema, derivado de la misma maestra.
    fernet_key = base64.urlsafe_b64encode(
        hashlib.sha256((master + "|fernet").encode()).digest()
    )
    siv_key = hashlib.sha512((master + "|siv").encode()).digest()  # 64B → AES-256-SIV
    return Fernet(fernet_key), AESSIV(siv_key)


def _ciphers() -> tuple[Fernet, AESSIV] | None:
    master = settings.pii_encryption_key
    if not master:
        return None
    pair = _cache.get(master)
    if pair is None:
        pair = _build(master)
        _cache[master] = pair
    return pair


def is_enabled() -> bool:
    return bool(settings.pii_encryption_key)


# --------------------------------------------------------------------------- #
#  Cifrado aleatorio (Fernet) — para campos que no se buscan
# --------------------------------------------------------------------------- #
def encrypt(value: str | None) -> str | None:
    if value is None:
        return None
    c = _ciphers()
    if c is None:
        return value
    return _ENC_PREFIX + c[0].encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.startswith(_ENC_PREFIX):
        return value  # texto legado en claro o passthrough
    c = _ciphers()
    if c is None:
        return value
    try:
        return c[0].decrypt(value[len(_ENC_PREFIX):].encode()).decode()
    except InvalidToken:
        return value


# --------------------------------------------------------------------------- #
#  Cifrado determinista (AES-SIV) — buscable por igualdad
# --------------------------------------------------------------------------- #
def det_encrypt(value: str | None) -> str | None:
    if value is None:
        return None
    c = _ciphers()
    if c is None:
        return value
    ct = c[1].encrypt(value.encode(), [])  # AAD vacío → salida determinista
    return _DET_PREFIX + base64.urlsafe_b64encode(ct).decode()


def det_decrypt(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.startswith(_DET_PREFIX):
        return value
    c = _ciphers()
    if c is None:
        return value
    try:
        ct = base64.urlsafe_b64decode(value[len(_DET_PREFIX):].encode())
        return c[1].decrypt(ct, []).decode()
    except (InvalidTag, InvalidToken, ValueError, binascii.Error):
        return value
