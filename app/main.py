"""Punto de entrada FastAPI del Agente de Citas."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import __version__
from app.agent.llm import LLMNotConfiguredError
from app.api import admin, agent, auth, metrics, reports, webhook
from app.config import settings
from app.database import init_db
from app.security import close_redis, rate_limit, require_admin, validate_path_ids

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("agente-citas")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # En desarrollo creamos el esquema al arrancar. En producción: Alembic.
    if not settings.is_production:
        await init_db()
        logger.info("Esquema de BD inicializado (modo desarrollo)")
    yield
    await close_redis()


# En producción ocultamos la documentación interactiva.
_docs = None if settings.is_production else "/docs"
_redoc = None if settings.is_production else "/redoc"

app = FastAPI(
    title="Agente de Citas",
    description=(
        "Backend genérico de reservas por WhatsApp "
        "para cualquier negocio con cita previa."
    ),
    version=__version__,
    lifespan=lifespan,
    docs_url=_docs,
    redoc_url=_redoc,
    openapi_url=None if settings.is_production else "/openapi.json",
)

# En producción, restringe los Host aceptados (anti DNS-rebinding / Host spoofing).
if settings.is_production and settings.allowed_hosts_list != ["*"]:
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts_list
    )


@app.middleware("http")
async def _harden(request: Request, call_next):
    # Rechaza cuerpos desmesurados (DoS) antes de procesarlos.
    cl = request.headers.get("content-length")
    if cl is not None and cl.isdigit() and int(cl) > settings.max_body_bytes:
        return JSONResponse(status_code=413, content={"detail": "Cuerpo demasiado grande"})
    response: Response = await call_next(request)
    # Cabeceras de seguridad estándar.
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "no-referrer")
    h.setdefault("Cache-Control", "no-store")
    h.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    h.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    # CSP estricta para el panel: scripts solo propios (sin inline → corta XSS),
    # sin CDNs externos. Los estilos en línea (atributos style) sí se permiten.
    if request.url.path.startswith("/panel"):
        h.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: https:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
            "manifest-src 'self'; form-action 'self'",
        )
    if settings.is_production:
        # HSTS solo en producción (allí sirve por HTTPS).
        h.setdefault(
            "Strict-Transport-Security",
            "max-age=63072000; includeSubDomains; preload",
        )
    return response


# Auditoría: registra escrituras admin y eventos de seguridad (401/403/429) en un
# log encadenado por hash. Decoupled del request (sesión propia) y a prueba de
# fallos: si el registro falla, la petición no se ve afectada.
_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
_SECURITY_STATUS = {401, 403, 429}
_AUDIT_PATH_PREFIXES = ("/admin", "/ask")


def _audit_action(method: str, path: str, status: int) -> str | None:
    if status in _SECURITY_STATUS and (
        path.startswith(_AUDIT_PATH_PREFIXES) or path.startswith("/webhook")
    ):
        return "security"
    if method in _MUTATING and path.startswith("/admin"):
        return "mutation"
    return None


@app.middleware("http")
async def _audit(request: Request, call_next):
    response: Response = await call_next(request)
    if not settings.audit_enabled:
        return response
    action = _audit_action(request.method, request.url.path, response.status_code)
    if action is None:
        return response
    try:
        from app.audit import business_from_path, key_fingerprint, record
        from app.database import AsyncSessionLocal
        from app.security import client_ip

        path = request.url.path
        async with AsyncSessionLocal() as session:
            await record(
                session,
                action=action,
                method=request.method,
                path=path[:512],
                status=response.status_code,
                actor_ip=client_ip(request),
                actor_key_fp=key_fingerprint(request.headers.get("x-api-key")),
                business_id=business_from_path(path),
            )
            await session.commit()
    except Exception:  # nunca romper la petición por un fallo de auditoría
        logger.warning("No se pudo registrar la auditoría", exc_info=True)
    return response


# Admin/agente/métricas: rate-limit (incluso para auth fallida → frena fuerza
# bruta) ANTES de exigir la API key.
# Login del panel: rate-limit + bloqueo por fallos, pero SIN require_admin (es la
# puerta de entrada). Debe registrarse antes que el router admin protegido.
app.include_router(auth.router, dependencies=[Depends(rate_limit("login"))])

_admin_guard = [
    Depends(rate_limit("admin")),
    Depends(require_admin),
    Depends(validate_path_ids),  # los *_id de ruta deben ser UUID válidos
]
app.include_router(admin.router, dependencies=_admin_guard)
app.include_router(agent.router, dependencies=_admin_guard)
app.include_router(metrics.router, dependencies=_admin_guard)
app.include_router(reports.router, dependencies=_admin_guard)
# El dashboard HTML (sin datos) solo se publica si está habilitado (no en prod).
if settings.dashboard_on:
    app.include_router(metrics.ui_router)
app.include_router(webhook.router)

# Panel de gestión (frontend estático). Las páginas no llevan datos: piden la API
# key en el navegador y consultan los endpoints protegidos.
_FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
if _FRONTEND.is_dir():
    app.mount("/panel", StaticFiles(directory=_FRONTEND, html=True), name="panel")


@app.exception_handler(LLMNotConfiguredError)
async def _llm_not_configured(
    _request: Request, exc: LLMNotConfiguredError
) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(SQLAlchemyError)
async def _db_error(_request: Request, exc: SQLAlchemyError) -> JSONResponse:
    # El detalle (consulta, esquema, mensaje del driver) SOLO va al log del
    # servidor; al cliente, un mensaje genérico. Nunca se revela la BD.
    logger.exception("Error de base de datos")
    return JSONResponse(status_code=500, content={"detail": "Error interno"})


@app.exception_handler(Exception)
async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Error no controlado")
    return JSONResponse(status_code=500, content={"detail": "Error interno"})


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__, "env": settings.app_env}


# Política de privacidad pública (la exige Meta para publicar la app).
@app.get("/privacidad", include_in_schema=False)
@app.get("/privacy", include_in_schema=False)
async def privacy_policy() -> Response:
    page = _FRONTEND / "privacidad.html"
    if not page.is_file():
        return JSONResponse(status_code=404, content={"detail": "No disponible"})
    return FileResponse(page, media_type="text/html")
