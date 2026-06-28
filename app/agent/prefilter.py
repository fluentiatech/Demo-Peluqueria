"""Cortes pre-LLM: resuelve por reglas los turnos triviales sin llamar al modelo.

Un saludo, un "gracias", un "sí/no" en la confirmación o "la 2" al elegir hueco
no necesitan NLU. Atajarlos aquí elimina la mayoría de las llamadas al LLM en una
conversación de reserva, que es la palanca de coste nº1 (Fase 3).

Es deliberadamente CONSERVADOR: ante la mínima duda devuelve None y el flujo cae
al LLM. Nunca debe cerrar una transacción por su cuenta (eso lo hace la FSM).
"""
from __future__ import annotations

import re
import unicodedata

from app.agent.llm import Extraction
from app.models import ConversationState as S

_AFFIRM = {
    "si", "sip", "sii", "vale", "ok", "oka", "okay", "okey", "dale", "claro",
    "perfecto", "correcto", "confirmo", "confirmar", "eso es", "de acuerdo",
    "genial", "estupendo", "ok gracias", "vale gracias",
}
_NEGATE = {"no", "nop", "mejor no", "nada", "negativo", "no gracias", "para nada"}
_GREET = {
    "hola", "buenas", "hey", "ey", "holi", "buenos dias", "buenas tardes",
    "buenas noches", "que tal", "saludos", "hola buenas",
}
_THANKS = {
    "gracias", "muchas gracias", "mil gracias", "perfecto gracias",
    "genial gracias", "gracias!",
}
_CHOICE = re.compile(r"^(?:la|el|opcion|numero|num|n)?\s*(\d{1,2})$")
_ANY_PRO = {
    "me da igual", "da igual", "cualquiera", "el que sea", "indiferente",
    "cualquier", "lo que sea", "me es igual",
}


def _norm(text: str) -> str:
    """Minúsculas, sin acentos ni signos de puntuación de borde."""
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    t = t.lower().strip()
    t = re.sub(r"^[¡!.,;:\s]+", "", t)
    t = re.sub(r"[!¡.,;:)('\"\s]+$", "", t)
    return t


def _ext(intent: str, **data: object) -> Extraction:
    return Extraction(data={"intent": intent, **data}, model="prefilter")


def fast_classify(state: S, text: str, has_offered: bool) -> Extraction | None:
    """Devuelve una extracción sin LLM, o None si el turno necesita el modelo."""
    has_thumbs = "👍" in text or "👌" in text
    t = _norm(text)
    if not t and not has_thumbs:
        return None

    if state in (S.CONFIRMING, S.WAITLIST_OFFER):
        if has_thumbs or t in _AFFIRM:
            return _ext("confirm")
        if t in _NEGATE:
            return _ext("deny")
        return None

    if state == S.COLLECTING_PROFESSIONAL and t in _ANY_PRO:
        return _ext("choose", professional="any")

    if state == S.COLLECTING_DATETIME and has_offered:
        m = _CHOICE.match(t)
        if m:
            return _ext("choose", choice_index=int(m.group(1)))
        return None

    if state == S.IDLE:
        if t in _GREET:
            return _ext("greeting")
        if t in _THANKS:
            return _ext("greeting")  # respuesta cordial breve, sin LLM

    return None
