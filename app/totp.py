"""TOTP (RFC 6238) con la librería estándar — segundo factor del panel.

Sin dependencias externas: HMAC-SHA1 sobre el contador de tiempo. Compatible con
Google Authenticator / Authy. La verificación admite ±`window` pasos para tolerar
el desfase de reloj.
"""
from __future__ import annotations

import base64
import binascii
import hmac
import struct
import time

_STEP = 30  # segundos por código
_DIGITS = 6


def _b32_decode(secret: str) -> bytes:
    # Acepta el secreto base32 con o sin padding y en minúsculas.
    s = secret.strip().replace(" ", "").upper()
    s += "=" * (-len(s) % 8)
    return base64.b32decode(s)


def _code_at(key: bytes, counter: int) -> str:
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, "sha1").digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10**_DIGITS)).zfill(_DIGITS)


def verify(secret: str, code: str, *, at: float | None = None, window: int = 1) -> bool:
    """¿`code` es válido para `secret` ahora (±window pasos)? Tiempo constante."""
    if not secret or not code:
        return False
    code = code.strip().replace(" ", "")
    if not code.isdigit():
        return False
    try:
        key = _b32_decode(secret)
    except (ValueError, binascii.Error):
        return False
    counter = int((at if at is not None else time.time()) // _STEP)
    ok = False
    for drift in range(-window, window + 1):
        # Recorre todos los candidatos (sin cortocircuito) para no filtrar timing.
        ok |= hmac.compare_digest(_code_at(key, counter + drift), code)
    return ok
