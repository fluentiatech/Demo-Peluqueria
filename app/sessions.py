"""Sesiones firmadas del panel + bloqueo de login por fuerza bruta.

El navegador del panel cambia la API key por una **cookie de sesión firmada**
(HttpOnly, SameSite=Strict): así un XSS no puede leerla (a diferencia de guardar
la clave en `sessionStorage`). El token se firma con HMAC-SHA256 (stdlib, sin
dependencias) e incluye un **token CSRF** que el panel reenvía en las escrituras.

El bloqueo por fallos frena el barrido de claves: tras `login_max_attempts`
fallos desde una IP, se rechaza durante `login_lockout_min` minutos.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from app.config import settings

_SEP = "."
# Secreto de proceso si no hay ninguno configurable estable (dev): las sesiones
# se invalidan al reiniciar, lo cual es aceptable.
_PROCESS_SECRET = os.urandom(32).hex()


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _secret() -> bytes:
    if settings.session_secret:
        return settings.session_secret.encode()
    # Deriva de las credenciales admin (estable entre reinicios si están fijadas).
    material = "|".join(
        settings.admin_api_keys_list + settings.admin_api_key_hashes_list
    )
    if material:
        return hashlib.sha256(material.encode()).digest()
    return _PROCESS_SECRET.encode()


def _sign(payload: str) -> str:
    mac = hmac.new(_secret(), payload.encode(), hashlib.sha256).digest()
    return _b64e(mac)


def issue_session(key_fp: str | None) -> tuple[str, str]:
    """Crea una sesión. Devuelve (cookie, csrf_token)."""
    csrf = secrets.token_urlsafe(24)
    body = {"fp": key_fp, "csrf": csrf, "iat": int(time.time())}
    payload = _b64e(json.dumps(body, separators=(",", ":")).encode())
    return f"{payload}{_SEP}{_sign(payload)}", csrf


def read_session(cookie: str | None) -> dict | None:
    """Valida la firma y la antigüedad. Devuelve el payload o None."""
    if not cookie or _SEP not in cookie:
        return None
    payload, sig = cookie.rsplit(_SEP, 1)
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    try:
        body = json.loads(_b64d(payload))
    except (ValueError, json.JSONDecodeError):
        return None
    iat = body.get("iat", 0)
    if not isinstance(iat, int) or time.time() - iat > settings.session_ttl_min * 60:
        return None
    return body


# --------------------------------------------------------------------------- #
#  Bloqueo de login por IP (en memoria)
# --------------------------------------------------------------------------- #
_failures: dict[str, list[float]] = {}


def clear_login_state() -> None:
    _failures.clear()


def is_locked(ip: str) -> bool:
    window = settings.login_lockout_min * 60
    cutoff = time.monotonic() - window
    recent = [t for t in _failures.get(ip, []) if t >= cutoff]
    _failures[ip] = recent
    return len(recent) >= settings.login_max_attempts


def register_failure(ip: str) -> None:
    _failures.setdefault(ip, []).append(time.monotonic())


def reset_failures(ip: str) -> None:
    _failures.pop(ip, None)


def cookie_secure() -> bool:
    if settings.cookie_secure is not None:
        return settings.cookie_secure
    return settings.is_production
