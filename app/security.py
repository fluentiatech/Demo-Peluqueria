"""Seguridad transversal: autenticación de la API admin y rate limiting.

- `require_admin`: protege la API de administración y el agente con una API key.
  Sin clave configurada se permite SOLO en desarrollo; en producción se rechaza
  (fail-closed) para no exponer datos por una mala configuración.
- `rate_limit`: limitador de ventana fija por IP. Backend configurable:
    * "memory" — contador en proceso (una sola instancia).
    * "redis"  — contador compartido entre réplicas; ante un fallo de Redis hace
      *fallback* al contador en memoria para no tumbar la API.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import re
import time
from typing import Any

from fastapi import Header, HTTPException, Request

from app.config import settings

logger = logging.getLogger("agente-citas.security")

# Formato UUID (las PK del modelo son uuid4). Valida los identificadores de ruta.
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def validate_path_ids(request: Request) -> None:
    """Rechaza identificadores de ruta (`*_id`) que no sean UUID válidos (422).

    Defensa en profundidad: las consultas ya van parametrizadas, pero esto corta
    de raíz cualquier valor inesperado antes de tocar la BD.
    """
    for name, value in request.path_params.items():
        if name.endswith("_id") and not _UUID_RE.match(str(value)):
            raise HTTPException(status_code=422, detail=f"Identificador inválido: {name}")

# Longitud mínima exigida a una API key (en claro) en producción.
MIN_API_KEY_LEN = 24


def _valid_admin_keys() -> list[str]:
    keys = settings.admin_api_keys_list
    if settings.is_production:
        # En producción solo cuentan las claves en claro suficientemente largas.
        keys = [k for k in keys if len(k) >= MIN_API_KEY_LEN]
    return keys


def _has_credentials() -> bool:
    return bool(_valid_admin_keys() or settings.admin_api_key_hashes_list)


def hash_key(key: str) -> str:
    """Hash SHA-256 (hex) de una API key, tal como se guarda en reposo."""
    return hashlib.sha256(key.encode()).hexdigest()


def _key_matches(provided: str) -> bool:
    # Comparación en tiempo constante contra claves en claro y contra hashes.
    for k in _valid_admin_keys():
        if hmac.compare_digest(provided, k):
            return True
    digest = hash_key(provided)
    return any(
        hmac.compare_digest(digest, h) for h in settings.admin_api_key_hashes_list
    )


def _ip_trusted(request: Request) -> bool:
    """¿Viene la petición de una red de confianza (LAN/VPN) sin necesidad de clave?"""
    cidrs = settings.trusted_admin_cidrs_list
    if not cidrs:
        return False
    try:
        ip = ipaddress.ip_address(client_ip(request))
    except ValueError:
        return False
    for c in cidrs:
        try:
            if ip in ipaddress.ip_network(c, strict=False):
                return True
        except ValueError:
            continue
    return False


_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def require_admin(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> None:
    # Red de confianza (el local o la VPN): acceso sin API key.
    if _ip_trusted(request):
        return

    # Cookie de sesión firmada (el camino del navegador del panel). En escrituras
    # exige el token CSRF (defensa en profundidad sobre SameSite=Strict).
    from app.sessions import read_session

    session = read_session(request.cookies.get("sid"))
    if session is not None:
        if request.method in _MUTATING_METHODS and (
            not x_csrf_token
            or not hmac.compare_digest(x_csrf_token, session.get("csrf", ""))
        ):
            raise HTTPException(status_code=403, detail="CSRF token inválido o ausente")
        return

    if not _has_credentials():
        if settings.is_production:
            raise HTTPException(
                status_code=503,
                detail="Autenticación de administración no configurada",
            )
        return  # desarrollo: abierta por comodidad
    # API key por cabecera: para clientes no-navegador (curl, integraciones).
    if not x_api_key or not _key_matches(x_api_key):
        # Log para detección de intentos (fuerza bruta / clave filtrada).
        logger.warning("Auth admin fallida desde %s", client_ip(request))
        raise HTTPException(status_code=401, detail="API key inválida o ausente")


# --------------------------------------------------------------------------- #
#  Rate limiting
# --------------------------------------------------------------------------- #
_WINDOW_S = 60.0


def client_ip(request: Request) -> str:
    """IP real del cliente para el rate limit.

    Tras un proxy/balanceador, `request.client.host` es la IP del proxy (todos
    compartirían cubo). Si `trust_proxy` está activo —y SOLO entonces, porque la
    cabecera es falsificable— se toma la primera IP de `X-Forwarded-For`.

    El valor se **valida como IP** (módulo `ipaddress`): así, aunque la cabecera
    traiga texto arbitrario (p. ej. un payload de inyección), nunca se usa tal
    cual como clave; se descarta a "invalid". Defensa en profundidad: esta clave
    solo alimenta el limitador, jamás una consulta SQL.
    """
    candidate: str | None = None
    if settings.trust_proxy:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            candidate = xff.split(",")[0].strip()
    if candidate is None and request.client:
        candidate = request.client.host
    if not candidate:
        return "unknown"
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return "invalid"

# Backend en memoria (también es el fallback si Redis falla).
_buckets: dict[str, list[float]] = {}

# Cliente Redis perezoso (se crea en el primer uso).
_redis: Any = None


def clear_rate_limit_state() -> None:
    """Reinicia el estado del limitador en memoria (usado en tests)."""
    _buckets.clear()


def _memory_allow(key: str, limit: int) -> bool:
    now = time.monotonic()
    cutoff = now - _WINDOW_S
    bucket = _buckets.setdefault(key, [])
    while bucket and bucket[0] < cutoff:
        bucket.pop(0)
    if len(bucket) >= limit:
        return False
    bucket.append(now)
    return True


def _get_redis() -> Any:
    global _redis
    if _redis is None:
        import redis.asyncio as aioredis

        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def close_redis() -> None:
    """Cierra el cliente Redis (al apagar la app)."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


async def _redis_allow(key: str, limit: int) -> bool:
    """Contador de ventana fija en Redis. Ante error, cae al backend en memoria."""
    try:
        client = _get_redis()
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, int(_WINDOW_S))
        return count <= limit
    except Exception:
        logger.warning("Redis no disponible; rate limit en memoria", exc_info=True)
        return _memory_allow(key, limit)


def rate_limit(scope: str):
    """Crea una dependencia que limita `rate_limit_per_min` peticiones/min por IP."""

    async def _dependency(request: Request) -> None:
        limit = settings.rate_limit_per_min
        if limit <= 0:
            return
        key = f"rl:{scope}:{client_ip(request)}"
        if settings.rate_limit_backend == "redis":
            allowed = await _redis_allow(key, limit)
        else:
            allowed = _memory_allow(key, limit)
        if not allowed:
            raise HTTPException(status_code=429, detail="Demasiadas peticiones")

    return _dependency
