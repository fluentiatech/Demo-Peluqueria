"""Cálculo de coste de las llamadas al LLM a partir de los tokens registrados.

Las tarifas son **estimaciones configurables** (USD por 1M de tokens): los precios
de OpenAI cambian, así que se pueden sobrescribir con `COST_PRICES_JSON` sin tocar
código. Los tokens cacheados se cobran a la tarifa reducida de entrada cacheada.
"""
from __future__ import annotations

import json
from functools import lru_cache

from app.config import settings

# USD por 1.000.000 de tokens. Valores por defecto orientativos; edítalos según
# la tarifa vigente de tu cuenta.
DEFAULT_PRICES: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.15, "cached": 0.075, "output": 0.60},
    "gpt-4o": {"input": 2.50, "cached": 1.25, "output": 10.00},
}

# Tarifa de respaldo para modelos desconocidos (la del modelo rápido).
_FALLBACK = {"input": 0.15, "cached": 0.075, "output": 0.60}


@lru_cache(maxsize=8)
def _parse_overrides(raw: str) -> dict[str, dict[str, float]]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def get_prices() -> dict[str, dict[str, float]]:
    return {**DEFAULT_PRICES, **_parse_overrides(settings.cost_prices_json)}


def price_for(model: str) -> dict[str, float]:
    return get_prices().get(model, _FALLBACK)


def cost_usd(
    model: str,
    prompt_tokens: int,
    cached_tokens: int,
    completion_tokens: int,
) -> float:
    """Coste estimado en USD de una llamada.

    `prompt_tokens` incluye los `cached_tokens`; la parte no cacheada se cobra a la
    tarifa de entrada y la cacheada a la reducida.
    """
    p = price_for(model)
    non_cached = max(prompt_tokens - cached_tokens, 0)
    total = (
        non_cached * p["input"]
        + cached_tokens * p["cached"]
        + completion_tokens * p["output"]
    )
    return total / 1_000_000
