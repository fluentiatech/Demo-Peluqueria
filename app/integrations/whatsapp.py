"""Cliente de la WhatsApp Cloud API (Meta): parseo de entrada y envío de texto."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger("agente-citas.whatsapp")

_GRAPH_URL = "https://graph.facebook.com/v21.0"


@dataclass
class IncomingMessage:
    phone_number_id: str  # número del negocio (destino) → enruta al tenant
    from_: str            # teléfono del cliente (E.164 sin '+')
    message_id: str       # wamid... → idempotencia
    text: str
    name: str | None = None


def parse_incoming(payload: dict[str, Any]) -> list[IncomingMessage]:
    """Extrae los mensajes de texto de un webhook de Meta.

    Ignora callbacks de estado (entregado/leído) y tipos no soportados.
    """
    out: list[IncomingMessage] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            phone_number_id = value.get("metadata", {}).get("phone_number_id", "")
            names = {
                c.get("wa_id"): c.get("profile", {}).get("name")
                for c in value.get("contacts", [])
            }
            for msg in value.get("messages", []):
                if msg.get("type") != "text":
                    continue
                sender = msg.get("from", "")
                out.append(
                    IncomingMessage(
                        phone_number_id=phone_number_id,
                        from_=sender,
                        message_id=msg.get("id", ""),
                        text=msg.get("text", {}).get("body", ""),
                        name=names.get(sender),
                    )
                )
    return out


async def send_text(to: str, text: str, phone_number_id: str) -> bool:
    """Envía un mensaje de texto. Sin credenciales (desarrollo) hace no-op + log."""
    if not settings.whatsapp_access_token:
        logger.info("[whatsapp:dev] -> %s: %s", to, text)
        return False

    # El phone_number_id de Meta es numérico; evita inyección en la ruta de Graph.
    if not phone_number_id.isdigit() or not to.isdigit():
        logger.error("Identificador no numérico: pnid=%r to=%r", phone_number_id, to)
        return False

    return await _post_message(
        phone_number_id,
        {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text},
        },
    )


async def send_template(
    to: str,
    template: str,
    params: list[str],
    phone_number_id: str,
    lang: str = "es",
) -> bool:
    """Envía una plantilla aprobada de Meta (para recordatorios fuera de 24 h)."""
    if not settings.whatsapp_access_token:
        logger.info("[whatsapp:dev] plantilla %s -> %s: %s", template, to, params)
        return False
    if not phone_number_id.isdigit() or not to.isdigit():
        logger.error("Identificador no numérico: pnid=%r to=%r", phone_number_id, to)
        return False

    body = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": lang},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": p} for p in params],
                }
            ],
        },
    }
    return await _post_message(phone_number_id, body)


# Códigos HTTP que merece la pena reintentar (transitorios).
_RETRYABLE = {408, 425, 429, 500, 502, 503, 504}


async def _post_message(phone_number_id: str, body: dict[str, Any]) -> bool:
    """Envía a Graph con reintentos y backoff exponencial ante errores transitorios.

    Un 4xx permanente (p. ej. número inválido) no se reintenta. Las redes/5xx/429
    sí, hasta `send_max_retries`, para no perder mensajes por un fallo pasajero.
    """
    url = f"{_GRAPH_URL}/{phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
    attempts = max(1, settings.send_max_retries)

    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, json=body, headers=headers)
            if resp.status_code < 400:
                return True
            if resp.status_code not in _RETRYABLE:
                logger.error(
                    "Envío WhatsApp rechazado (%s): %s", resp.status_code, resp.text
                )
                return False  # permanente: no reintentar
            logger.warning(
                "Envío WhatsApp transitorio (%s), intento %d/%d",
                resp.status_code, attempt + 1, attempts,
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "Error de red enviando WhatsApp (%s), intento %d/%d",
                exc, attempt + 1, attempts,
            )
        if attempt < attempts - 1:
            await asyncio.sleep(settings.send_retry_backoff_s * (2**attempt))

    logger.error("Envío WhatsApp agotó los reintentos")
    return False
