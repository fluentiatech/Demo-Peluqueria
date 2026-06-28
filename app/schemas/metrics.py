"""Schemas de salida del panel de métricas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class DailyCost(BaseModel):
    date: str  # YYYY-MM-DD
    calls: int
    prompt_tokens: int
    cached_tokens: int
    completion_tokens: int
    cost_usd: float


class ModelCost(BaseModel):
    model: str
    calls: int
    prompt_tokens: int
    cached_tokens: int
    completion_tokens: int
    cost_usd: float


class KindCost(BaseModel):
    kind: str  # "nlu" | "qa" | ...
    calls: int
    cost_usd: float


class MetricsSummary(BaseModel):
    period_days: int
    since: datetime
    currency: str = "USD"

    # LLM
    llm_calls: int
    prompt_tokens: int
    cached_tokens: int
    completion_tokens: int
    total_cost_usd: float
    cost_per_conversation_usd: float

    by_model: list[ModelCost]
    by_kind: list[KindCost]
    by_day: list[DailyCost]

    # Actividad operativa
    inbound_messages: int
    conversations: int
    appointments_created: int
    handoffs: int
    errors: int

    # Optimización de coste: turnos resueltos por reglas, sin llamar al LLM.
    prefiltered_turns: int
