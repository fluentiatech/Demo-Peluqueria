"""Login del panel: cambia la API key (y TOTP opcional) por una cookie de sesión.

No va detrás de `require_admin` (es la puerta de entrada), pero sí del rate-limit
y de un bloqueo por fallos para frenar la fuerza bruta.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from app import totp
from app.audit import key_fingerprint
from app.config import settings
from app.schemas.session import LoginIn, LoginOut
from app.security import _key_matches, client_ip
from app.sessions import (
    cookie_secure,
    is_locked,
    issue_session,
    register_failure,
    reset_failures,
)

router = APIRouter(prefix="/admin", tags=["auth"])

COOKIE_NAME = "sid"


@router.post("/session", response_model=LoginOut)
async def login(payload: LoginIn, request: Request, response: Response) -> LoginOut:
    ip = client_ip(request)
    if is_locked(ip):
        raise HTTPException(429, "Demasiados intentos. Inténtalo más tarde.")

    valid = _key_matches(payload.api_key)
    if settings.admin_totp_secret:
        valid = valid and totp.verify(settings.admin_totp_secret, payload.totp or "")

    if not valid:
        register_failure(ip)
        raise HTTPException(401, "Credenciales inválidas")

    reset_failures(ip)
    cookie, csrf = issue_session(key_fingerprint(payload.api_key))
    response.set_cookie(
        COOKIE_NAME,
        cookie,
        max_age=settings.session_ttl_min * 60,
        httponly=True,
        samesite="strict",
        secure=cookie_secure(),
        path="/",
    )
    return LoginOut(csrf=csrf, ttl_min=settings.session_ttl_min)


@router.get("/session", response_model=LoginOut)
async def session_info(request: Request) -> LoginOut:
    """Devuelve el token CSRF de la sesión vigente (cookie ya válida).

    El panel lo llama al cargar cada página para recuperar el CSRF sin tener
    que volver a iniciar sesión; las navegaciones entre páginas son recargas
    completas y el token solo vive en memoria.
    """
    from app.sessions import read_session

    session = read_session(request.cookies.get(COOKIE_NAME))
    if session is None:
        raise HTTPException(401, "Sin sesión")
    return LoginOut(csrf=session.get("csrf", ""), ttl_min=settings.session_ttl_min)


@router.delete("/session", status_code=204)
async def logout(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")
