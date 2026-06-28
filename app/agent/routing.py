"""Routing de modelo: usa el modelo rápido por defecto y escala solo cuando hace
falta. La mayoría de los mensajes son rutinarios (precio, horario), así que el
modelo barato cubre el grueso y el caro se reserva para preguntas complejas.
"""
from __future__ import annotations

import re

from app.config import settings

# Señales de pregunta compleja (razonamiento, comparación, recomendación, etc.).
_COMPLEX = re.compile(
    r"\b(por qu[eé]|porqu[eé]|explica|expl[ií]came|diferencia|compar|"
    r"recomi|recomienda|aconseja|cu[aá]l es mejor|ventaja|mejor opci[oó]n|"
    r"deber[ií]a|qu[eé] me conviene)\b",
    re.IGNORECASE,
)

_LONG_QUESTION_CHARS = 180


def choose_model(text: str) -> str:
    """Elige el modelo para una respuesta de Q&A según la complejidad del texto."""
    t = text.strip()
    if len(t) > _LONG_QUESTION_CHARS or _COMPLEX.search(t):
        return settings.openai_model_smart
    return settings.openai_model_fast
