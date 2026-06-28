"""Webhook de la WhatsApp Cloud API (Meta).

Verifica el token y la firma, responde 200 al instante (Meta reintenta si tardas)
y procesa el mensaje en segundo plano con el agente de Q&A.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response

from app.agent.handler import process_incoming
from app.agent.llm import get_llm_client
from app.config import settings
from app.database import AsyncSessionLocal
from app.integrations.whatsapp import send_text
from app.security import rate_limit

logger = logging.getLogger("agente-citas.webhook")

router = APIRouter(prefix="/webhook", tags=["whatsapp"])


async def _process_in_background(payload: dict[str, Any]) -> None:
    """Tarea de fondo: sesión propia + cliente LLM + envío real."""
    try:
        llm = get_llm_client()
    except RuntimeError:
        logger.warning("Sin OPENAI_API_KEY: no se procesa el mensaje entrante")
        return
    async with AsyncSessionLocal() as session:
        try:
            await process_incoming(session, payload, llm, send_text)
        except Exception:
            logger.exception("Error procesando el webhook entrante")


@router.get("")
async def verify(request: Request) -> Response:
    """Handshake de verificación que Meta hace al registrar el webhook."""
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge", "")

    token_ok = token is not None and hmac.compare_digest(
        token, settings.whatsapp_verify_token
    )
    if mode == "subscribe" and token_ok:
        return Response(content=challenge, media_type="text/plain")
    return Response(status_code=403, content="verification failed")


def _valid_signature(body: bytes, signature_header: str | None) -> bool:
    """Comprueba la firma X-Hub-Signature-256 con el App Secret.

    Sin secreto configurado: se acepta SOLO en desarrollo. En producción se exige
    firma (fail-closed) para no procesar mensajes falsificados.
    """
    if not settings.whatsapp_app_secret:
        return not settings.is_production
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        settings.whatsapp_app_secret.encode(), body, hashlib.sha256
    ).hexdigest()
    received = signature_header.split("=", 1)[1]
    return hmac.compare_digest(expected, received)


@router.post("", dependencies=[Depends(rate_limit("webhook"))])
async def receive(request: Request, background: BackgroundTasks) -> Response:
    """Recibe los eventos de WhatsApp. Devuelve 200 rápido (Meta reintenta)."""
    body = await request.body()
    if not _valid_signature(body, request.headers.get("X-Hub-Signature-256")):
        return Response(status_code=403)

    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        payload = {}

    # El procesamiento (LLM + envío) va en segundo plano: a Meta le respondemos ya.
    background.add_task(_process_in_background, payload)
    return Response(status_code=200, content="EVENT_RECEIVED", media_type="text/plain")
