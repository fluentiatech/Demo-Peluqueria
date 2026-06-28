"""Agrega los eventos en métricas de coste y actividad para el panel.

La agregación de tokens se hace **en la BD** (GROUP BY sobre los campos JSON del
evento), no cargando todos los eventos en memoria: escala a millones de llamadas.
El coste se calcula en Python sobre el resultado ya agrupado (pocas filas), porque
depende de las tarifas por modelo.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.metrics.cost import cost_usd
from app.models import Appointment, EventLog, InboundMessage
from app.schemas.metrics import (
    DailyCost,
    KindCost,
    MetricsSummary,
    ModelCost,
)


def _round(value: float) -> float:
    return round(value, 6)


def _toks(key: str):
    return func.coalesce(EventLog.payload[key].as_integer(), 0)


async def _count(session: AsyncSession, model, *, since, business_id, extra=None) -> int:
    query = select(func.count()).select_from(model).where(model.created_at >= since)
    if business_id:
        query = query.where(model.business_id == business_id)
    if extra is not None:
        query = query.where(extra)
    return int((await session.scalar(query)) or 0)


async def collect_summary(
    session: AsyncSession,
    *,
    business_id: str | None = None,
    days: int = 30,
    now: datetime | None = None,
) -> MetricsSummary:
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)

    model_c = func.coalesce(EventLog.payload["model"].as_string(), "desconocido")
    kind_c = func.coalesce(EventLog.payload["kind"].as_string(), "otro")
    day_c = func.date(EventLog.created_at)
    filters = [EventLog.type == "llm_call", EventLog.created_at >= since]
    if business_id:
        filters.append(EventLog.business_id == business_id)

    # --- Agregado por (día, modelo): de aquí salen totales, por-modelo y por-día ---
    rows_dm = (
        await session.execute(
            select(
                day_c.label("day"),
                model_c.label("model"),
                func.count().label("calls"),
                func.sum(_toks("prompt_tokens")).label("p"),
                func.sum(_toks("cached_tokens")).label("c"),
                func.sum(_toks("completion_tokens")).label("o"),
            )
            .where(*filters)
            .group_by(day_c, model_c)
        )
    ).all()

    by_model: dict[str, dict[str, float]] = defaultdict(
        lambda: {"calls": 0, "p": 0, "c": 0, "o": 0, "cost": 0.0}
    )
    by_day: dict[str, dict[str, float]] = defaultdict(
        lambda: {"calls": 0, "p": 0, "c": 0, "o": 0, "cost": 0.0}
    )
    totals = {"calls": 0, "p": 0, "c": 0, "o": 0, "cost": 0.0}

    for r in rows_dm:
        p, c, o = int(r.p or 0), int(r.c or 0), int(r.o or 0)
        cost = cost_usd(r.model, p, c, o)
        day = str(r.day)
        for bucket in (by_model[r.model], by_day[day]):
            bucket["calls"] += r.calls
            bucket["p"] += p
            bucket["c"] += c
            bucket["o"] += o
            bucket["cost"] += cost
        totals["calls"] += r.calls
        totals["p"] += p
        totals["c"] += c
        totals["o"] += o
        totals["cost"] += cost

    # --- Agregado por (tipo, modelo) para el desglose por kind ---
    rows_km = (
        await session.execute(
            select(
                kind_c.label("kind"),
                model_c.label("model"),
                func.count().label("calls"),
                func.sum(_toks("prompt_tokens")).label("p"),
                func.sum(_toks("cached_tokens")).label("c"),
                func.sum(_toks("completion_tokens")).label("o"),
            )
            .where(*filters)
            .group_by(kind_c, model_c)
        )
    ).all()
    by_kind: dict[str, dict[str, float]] = defaultdict(
        lambda: {"calls": 0, "cost": 0.0}
    )
    for r in rows_km:
        cost = cost_usd(r.model, int(r.p or 0), int(r.c or 0), int(r.o or 0))
        by_kind[r.kind]["calls"] += r.calls
        by_kind[r.kind]["cost"] += cost

    # --- Actividad operativa (contadores) ---
    inbound = await _count(session, InboundMessage, since=since, business_id=business_id)
    appointments = await _count(session, Appointment, since=since, business_id=business_id)
    handoffs = await _count(
        session, EventLog, since=since, business_id=business_id,
        extra=EventLog.type == "handoff",
    )
    errors = await _count(
        session, EventLog, since=since, business_id=business_id,
        extra=EventLog.type == "error",
    )
    prefiltered = await _count(
        session, EventLog, since=since, business_id=business_id,
        extra=EventLog.type == "prefilter",
    )
    conv_query = select(func.count(func.distinct(InboundMessage.from_phone))).where(
        InboundMessage.created_at >= since
    )
    if business_id:
        conv_query = conv_query.where(InboundMessage.business_id == business_id)
    conversations = int((await session.scalar(conv_query)) or 0)

    cost_per_conv = totals["cost"] / conversations if conversations else 0.0

    return MetricsSummary(
        period_days=days,
        since=since,
        llm_calls=int(totals["calls"]),
        prompt_tokens=int(totals["p"]),
        cached_tokens=int(totals["c"]),
        completion_tokens=int(totals["o"]),
        total_cost_usd=_round(totals["cost"]),
        cost_per_conversation_usd=_round(cost_per_conv),
        by_model=[
            ModelCost(
                model=m,
                calls=int(v["calls"]),
                prompt_tokens=int(v["p"]),
                cached_tokens=int(v["c"]),
                completion_tokens=int(v["o"]),
                cost_usd=_round(v["cost"]),
            )
            for m, v in sorted(by_model.items(), key=lambda kv: -kv[1]["cost"])
        ],
        by_kind=[
            KindCost(kind=k, calls=int(v["calls"]), cost_usd=_round(v["cost"]))
            for k, v in sorted(by_kind.items(), key=lambda kv: -kv[1]["cost"])
        ],
        by_day=[
            DailyCost(
                date=d,
                calls=int(v["calls"]),
                prompt_tokens=int(v["p"]),
                cached_tokens=int(v["c"]),
                completion_tokens=int(v["o"]),
                cost_usd=_round(v["cost"]),
            )
            for d, v in sorted(by_day.items())
        ],
        inbound_messages=inbound,
        conversations=conversations,
        appointments_created=appointments,
        handoffs=handoffs,
        errors=errors,
        prefiltered_turns=prefiltered,
    )
