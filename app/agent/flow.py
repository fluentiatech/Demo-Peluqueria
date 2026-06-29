"""Máquina de estados de la conversación de reserva.

El LLM (vía `nlu.classify`) solo aporta intención y entidades. Esta FSM gobierna
el flujo y ejecuta las acciones con efectos a través de las tools deterministas
(`check_availability`, `book_appointment`, …), que validan todo. El modelo nunca
cierra una transacción: por eso una inyección de prompt no puede forzar una cita.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import timez
from app.agent import replies
from app.agent.context import ResourceRef, ServiceRef, get_business_context
from app.agent.llm import Extraction, LLMClient
from app.agent.nlu import Intent, classify
from app.agent.prefilter import fast_classify
from app.agent.qa import answer_question
from app.config import settings
from app.models import (
    Appointment,
    AppointmentStatus,
    Business,
    Conversation,
    ConversationState,
    Customer,
    EventLog,
    Resource,
    Service,
)
from app.tools import (
    BookingError,
    SlotTakenError,
    add_to_waitlist,
    book_appointment,
    cancel_appointment,
    check_availability,
    free_resources_at,
    reschedule_appointment,
)

logger = logging.getLogger("agente-citas.flow")

MAX_OFFER = 6
WINDOW_TURNS = 6  # ventana deslizante: últimos N mensajes conservados
S = ConversationState


def _normalize_phone(phone: str) -> str:
    phone = phone.strip()
    return phone if phone.startswith("+") else f"+{phone}"


def _push_window(window: list, role: str, text: str) -> list:
    """Añade un turno a la ventana deslizante acotada (anti-crecimiento de contexto)."""
    out = [*window, {"role": role, "text": (text or "")[:200]}]
    return out[-WINDOW_TURNS:]


def _convo_select(business_id: str, phone: str, lock: bool) -> Select:
    stmt = select(Conversation).where(
        Conversation.business_id == business_id,
        Conversation.customer_phone == phone,
    )
    # En Postgres bloqueamos la fila (FOR UPDATE) para serializar los turnos
    # concurrentes del mismo teléfono y evitar el lost-update de estado/context.
    # SQLite serializa por sí mismo, así que el lock es innecesario allí.
    return stmt.with_for_update() if lock else stmt


async def _load_convo(
    session: AsyncSession, business_id: str, phone: str
) -> Conversation:
    stmt = _convo_select(business_id, phone, lock=not settings.is_sqlite)
    convo = await session.scalar(stmt)
    if convo is not None:
        return convo

    # No existía: la creamos. Si otra entrega concurrente la crea a la vez, el
    # UNIQUE(business_id, customer_phone) falla en el savepoint y reusamos la suya.
    try:
        async with session.begin_nested():
            convo = Conversation(
                business_id=business_id,
                customer_phone=phone,
                state=S.IDLE,
                context={},
            )
            session.add(convo)
            await session.flush()
        return convo
    except IntegrityError:
        existing = await session.scalar(stmt)
        if existing is None:
            raise
        return existing


def _set(convo: Conversation, state: ConversationState, ctx: dict[str, Any]) -> None:
    convo.state = state
    convo.context = ctx  # reasignar para que SQLAlchemy detecte el cambio


def _match_service(
    services: tuple[ServiceRef, ...], name: str | None
) -> ServiceRef | None:
    if not name:
        return None
    low = name.strip().lower()
    for s in services:
        if s.name.lower() == low:
            return s
    for s in services:
        if low in s.name.lower() or s.name.lower() in low:
            return s
    return None


def _resolve_date(ext: Extraction) -> date | None:
    raw = ext.data.get("date")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None


def _pick_slot(offered: list[dict], idx: Any, time_str: Any) -> dict | None:
    if isinstance(idx, int) and 1 <= idx <= len(offered):
        return offered[idx - 1]
    if time_str:
        for o in offered:
            if datetime.fromisoformat(o["start_at"]).strftime("%H:%M") == str(time_str):
                return o
    return None


async def _find_slots(
    session: AsyncSession,
    business_id: str,
    service_id: str,
    day: date,
    resource_id: str | None = None,
) -> list:
    slots = await check_availability(
        session, business_id, service_id, day, day,
        resource_id=resource_id, limit=MAX_OFFER,
    )
    if not slots:
        slots = await check_availability(
            session, business_id, service_id, day, day + timedelta(days=6),
            resource_id=resource_id, limit=MAX_OFFER,
        )
    return slots


def _offered_from_slots(slots: list) -> list[dict]:
    return [
        {
            "resource_id": s.resource_id,
            "start_at": s.start_at.isoformat(),
            "label": replies.fmt_slot(s.start_at),
        }
        for s in slots
    ]


async def _customer_name(
    session: AsyncSession, business_id: str, phone: str
) -> str | None:
    """Nombre guardado del cliente con ese teléfono (None si es un número nuevo)."""
    customer = await session.scalar(
        select(Customer).where(
            Customer.business_id == business_id, Customer.phone == phone
        )
    )
    return customer.name if customer is not None else None


async def _upcoming_appointments(
    session: AsyncSession, business_id: str, phone: str
) -> list[Appointment]:
    customer = await session.scalar(
        select(Customer).where(
            Customer.business_id == business_id, Customer.phone == phone
        )
    )
    if customer is None:
        return []
    rows = await session.scalars(
        select(Appointment)
        .where(
            Appointment.business_id == business_id,
            Appointment.customer_id == customer.id,
            Appointment.status.in_(
                (AppointmentStatus.PENDING, AppointmentStatus.CONFIRMED)
            ),
            Appointment.start_at >= timez.now(),
        )
        .order_by(Appointment.start_at)
    )
    return list(rows)


def _log_call(session: AsyncSession, business_id: str, kind: str, ext: Extraction) -> None:
    session.add(
        EventLog(
            business_id=business_id,
            type="llm_call",
            payload={
                "kind": kind,
                "model": ext.model,
                "prompt_tokens": ext.prompt_tokens,
                "cached_tokens": ext.cached_tokens,
                "completion_tokens": ext.completion_tokens,
            },
        )
    )


async def handle_message(
    session: AsyncSession,
    business: Business,
    phone: str,
    text: str,
    llm: LLMClient,
    message_id: str | None = None,
) -> str:
    """Procesa un turno de conversación y devuelve la respuesta (o "" si no hay)."""
    phone = _normalize_phone(phone)
    convo = await _load_convo(session, business.id, phone)

    # Mientras un humano atiende, el bot calla: sin LLM, sin coste.
    if convo.state == S.HUMAN_HANDOFF:
        return ""

    # Contexto del negocio (catálogo/prompt/profesionales) desde la caché.
    bctx = await get_business_context(session, business)
    services, professionals = bctx.services, bctx.professionals
    base_ctx = dict(convo.context or {})
    window = _push_window(base_ctx.get("window", []), "user", text)
    has_offered = bool(base_ctx.get("offered"))

    # Corte pre-LLM: los turnos triviales se resuelven sin llamar al modelo.
    ext = fast_classify(convo.state, text, has_offered)
    if ext is not None:
        session.add(
            EventLog(
                business_id=business.id,
                type="prefilter",
                payload={"intent": ext.data.get("intent")},
            )
        )
    else:
        ext = await classify(
            llm,
            services=[s.name for s in services],
            professionals=[p.name for p in professionals],
            state=convo.state.value,
            text=text,
            today=date.today(),
        )
        _log_call(session, business.id, "nlu", ext)

    intent = ext.data.get("intent", Intent.OTHER.value)

    # --- Transiciones globales ---
    if intent == Intent.HANDOFF.value:
        # La ventana reciente acompaña al handoff para dar contexto al humano.
        _set(convo, S.HUMAN_HANDOFF, {"window": window})
        session.add(
            EventLog(
                business_id=business.id,
                type="handoff",
                payload={"phone": phone, "window": window},
            )
        )
        await session.flush()
        return replies.handoff()

    reply = await _dispatch(
        session, business, convo, phone, text, intent, ext, services,
        professionals, base_ctx, message_id, llm,
    )
    # Conserva la ventana deslizante acotada en el estado resultante.
    if reply:
        window = _push_window(window, "assistant", reply)
    convo.context = {**(convo.context or {}), "window": window}
    await session.flush()
    return reply


async def _dispatch(
    session: AsyncSession,
    business: Business,
    convo: Conversation,
    phone: str,
    text: str,
    intent: str,
    ext: Extraction,
    services: tuple[ServiceRef, ...],
    professionals: tuple[ResourceRef, ...],
    ctx: dict[str, Any],
    message_id: str | None,
    llm: LLMClient,
) -> str:
    state = convo.state

    # Abortar un flujo en curso si el cliente lo cancela explícitamente.
    # Excepción: en las ofertas escalonadas un "no" no aborta, significa
    # «pásame a la siguiente alternativa» (lo gestiona el handler del estado).
    aborting = intent in (Intent.CANCEL.value, Intent.DENY.value)
    _offer_states = (S.OFFER_ALT_PRO, S.OFFER_NEAREST)
    deny_is_next = state in _offer_states and intent == Intent.DENY.value
    if state != S.IDLE and aborting and not ctx.get("action") and not deny_is_next:
        _set(convo, S.IDLE, {})
        return replies.aborted()

    if state == S.IDLE:
        return await _idle(
            session, business, convo, intent, ext, services, professionals,
            ctx, phone, text, llm,
        )

    if state == S.COLLECTING_SERVICE:
        return await _collecting_service(
            session, business, convo, ext, services, professionals, ctx
        )

    if state == S.COLLECTING_PROFESSIONAL:
        return await _collecting_professional(
            session, business, convo, ext, professionals, ctx
        )

    if state == S.COLLECTING_DATETIME:
        return await _collecting_datetime(
            session, business, convo, ext, intent, ctx, professionals
        )

    if state == S.COLLECTING_CONTACT:
        return _collecting_contact(convo, ext, ctx)

    if state == S.OFFER_ALT_PRO:
        return await _offer_alt_pro(session, business, convo, intent, ctx)

    if state == S.OFFER_NEAREST:
        return await _offer_nearest(session, business, convo, intent, ctx)

    if state == S.WAITLIST_OFFER:
        return await _waitlist_offer(session, business, convo, phone, intent, ctx)

    if state == S.CONFIRMING:
        return await _confirming(session, business, convo, phone, intent, ctx, message_id)

    if state == S.MANAGE_BOOKING:
        return await _confirming(session, business, convo, phone, intent, ctx, message_id)

    return replies.fallback()


async def _idle(
    session, business, convo, intent, ext, services, professionals, ctx, phone, text, llm
) -> str:
    if intent == Intent.GREETING.value:
        # Número conocido → le saludamos por su nombre; número nuevo → saludo neutro.
        # El asistente se presenta con su nombre y tono configurados por el negocio.
        return replies.greeting(
            await _customer_name(session, business.id, phone),
            assistant_name=business.assistant_name,
            use_emojis=business.use_emojis,
        )

    if intent == Intent.BOOK.value:
        service = _match_service(services, ext.data.get("service"))
        if service is None:
            _set(convo, S.COLLECTING_SERVICE, {"mode": "book"})
            return replies.ask_service([s.name for s in services])
        return await _after_service(
            session, business, convo, ext, {"mode": "book"}, service, professionals
        )

    if intent in (Intent.CANCEL.value, Intent.RESCHEDULE.value):
        return await _start_manage(
            session, business, convo, phone, intent, professionals
        )

    # question / other / choose sin contexto → Q&A libre.
    return await answer_question(session, business, text, llm)


def _match_resource(
    professionals: tuple[ResourceRef, ...], name: str | None
) -> ResourceRef | str | None:
    """Devuelve el profesional pedido, "ANY" si le da igual, o None si no resuelve."""
    if not name:
        return None
    low = name.strip().lower()
    if low in {"any", "cualquiera", "el que sea", "me da igual", "da igual",
               "indiferente", "cualquier"}:
        return "ANY"
    for r in professionals:
        if r.name.lower() == low:
            return r
    for r in professionals:
        if low in r.name.lower() or r.name.lower() in low:
            return r
    return None


async def _after_service(
    session: AsyncSession,
    business: Business,
    convo: Conversation,
    ext: Extraction,
    ctx: dict[str, Any],
    service: ServiceRef,
    professionals: tuple[ResourceRef, ...],
) -> str:
    """Fijado el servicio, resuelve el profesional (preguntando si hace falta)."""
    ctx = {**ctx, "service_id": service.id, "service_name": service.name}
    pro = _match_resource(professionals, ext.data.get("professional"))
    if pro == "ANY":
        return await _begin_datetime(
            session, business, convo, ext, {**ctx, "any_pro": True}, professionals
        )
    if isinstance(pro, ResourceRef):
        ctx = {**ctx, "resource_id": pro.id, "professional_name": pro.name}
        return await _begin_datetime(session, business, convo, ext, ctx, professionals)
    # No lo dijo (o no se reconoce): preguntamos.
    _set(convo, S.COLLECTING_PROFESSIONAL, ctx)
    return replies.ask_professional([p.name for p in professionals])


async def _collecting_professional(
    session: AsyncSession,
    business: Business,
    convo: Conversation,
    ext: Extraction,
    professionals: tuple[ResourceRef, ...],
    ctx: dict[str, Any],
) -> str:
    pro = _match_resource(professionals, ext.data.get("professional"))
    if pro == "ANY":
        ctx = {**ctx, "any_pro": True}
    elif isinstance(pro, ResourceRef):
        ctx = {**ctx, "resource_id": pro.id, "professional_name": pro.name}
    else:
        return replies.professional_not_found([p.name for p in professionals])
    _set(convo, S.COLLECTING_DATETIME, ctx)
    return replies.ask_date(ctx["service_name"])


async def _begin_datetime(
    session: AsyncSession,
    business: Business,
    convo: Conversation,
    ext: Extraction,
    ctx: dict[str, Any],
    professionals: tuple[ResourceRef, ...],
) -> str:
    """Con servicio y profesional fijados; si ya hay fecha, oferta huecos ya."""
    if _resolve_date(ext) is None:
        _set(convo, S.COLLECTING_DATETIME, ctx)
        return replies.ask_date(ctx["service_name"])
    return await _collecting_datetime(
        session, business, convo, ext, Intent.CHOOSE.value, ctx, professionals
    )


async def _collecting_service(
    session: AsyncSession,
    business: Business,
    convo: Conversation,
    ext: Extraction,
    services: tuple[ServiceRef, ...],
    professionals: tuple[ResourceRef, ...],
    ctx: dict[str, Any],
) -> str:
    service = _match_service(services, ext.data.get("service"))
    if service is None:
        return replies.service_not_found([s.name for s in services])
    return await _after_service(session, business, convo, ext, ctx, service, professionals)


async def _collecting_datetime(
    session, business, convo, ext, intent, ctx, professionals
) -> str:
    # El cliente puede cambiar de profesional en cualquier momento del flujo:
    # si nombra a otro (o «me da igual»), reajustamos y volvemos a buscar.
    prev_day = None
    if ctx.get("offered"):
        prev_day = datetime.fromisoformat(ctx["offered"][0]["start_at"]).date()
    pro = _match_resource(professionals, ext.data.get("professional"))
    if isinstance(pro, ResourceRef) and pro.id != ctx.get("resource_id"):
        ctx = {**ctx, "resource_id": pro.id, "professional_name": pro.name,
               "any_pro": False}
        ctx.pop("offered", None)
    elif pro == "ANY" and not ctx.get("any_pro"):
        ctx = {**ctx, "any_pro": True, "resource_id": None}
        ctx.pop("offered", None)

    # Sub-paso 1: aún no hay huecos ofertados → necesitamos día (y quizá hora).
    if not ctx.get("offered"):
        day = _resolve_date(ext) or prev_day
        want = _norm_time(ext.data.get("time"))
        if day is None:
            return "¿Qué *día* te viene bien? _(p. ej. mañana, el viernes, 30/06)_"
        if want:
            return await _request_specific_time(session, business, convo, ctx, day, want)
        slots = await _find_slots(
            session, business.id, ctx["service_id"], day, ctx.get("resource_id")
        )
        if not slots:
            return await _go_waitlist(session, business, convo, ctx, day)
        ctx = {**ctx, "offered": _offered_from_slots(slots)}
        _set(convo, S.COLLECTING_DATETIME, ctx)
        return replies.offer_slots([o["label"] for o in ctx["offered"]])

    # Sub-paso 2: hay huecos ofertados → elige uno, o propone fecha/hora nuevas.
    chosen = _pick_slot(ctx["offered"], ext.data.get("choice_index"), ext.data.get("time"))
    if chosen is None:
        # El cliente puede sugerir otra hora/día en vez de elegir de la lista.
        if _resolve_date(ext) is not None or ext.data.get("time"):
            return await _propose_new(session, business, convo, ext, ctx)
        return replies.ask_choice_again()
    return await _choose_slot(session, business, convo, ctx, dict(chosen))


def _norm_time(value: Any) -> str | None:
    """Normaliza una hora a 'HH:MM' ('10' → '10:00', '9:5' → '09:05')."""
    if not value:
        return None
    s = str(value).strip()
    try:
        if ":" in s:
            h, m = s.split(":")[:2]
            return f"{int(h):02d}:{int(m):02d}"
        if s.isdigit():
            return f"{int(s):02d}:00"
    except ValueError:
        return None
    return None


async def _choose_slot(session, business, convo, ctx: dict[str, Any], chosen: dict) -> str:
    """Fija el hueco elegido y pasa a confirmar (o pide el nombre si es nuevo)."""
    # "Me da igual" en una RESERVA nueva: no fijamos recurso, lo elige el balanceo.
    # Al REPROGRAMAR sí respetamos el recurso del hueco elegido (cambio de profesional).
    if ctx.get("any_pro") and ctx.get("mode") != "reschedule":
        chosen["resource_id"] = None
    ctx = {**ctx, "chosen": chosen}
    ctx.pop("offered", None)

    if ctx.get("mode") == "reschedule":
        ctx["action"] = "reschedule"
        _set(convo, S.CONFIRMING, ctx)
        return replies.confirm_booking(ctx["service_name"], chosen["label"])

    name = await _customer_name(session, business.id, convo.customer_phone)
    if name:
        ctx = {**ctx, "name": name, "action": "book"}
        _set(convo, S.CONFIRMING, ctx)
        return replies.confirm_booking(ctx["service_name"], chosen["label"])
    _set(convo, S.COLLECTING_CONTACT, ctx)
    return replies.ask_name()


async def _propose_new(session, business, convo, ext, ctx: dict[str, Any]) -> str:
    """El cliente propone una fecha u hora distintas: re-buscamos disponibilidad."""
    day = _resolve_date(ext)
    if day is None and ctx.get("offered"):
        day = datetime.fromisoformat(ctx["offered"][0]["start_at"]).date()
    if day is None:
        return replies.ask_choice_again()

    want = _norm_time(ext.data.get("time"))
    if want:
        # Hora concreta: la resolvemos con el escalado profesional/cercano/espera.
        return await _request_specific_time(session, business, convo, ctx, day, want)

    slots = await check_availability(
        session, business.id, ctx["service_id"], day, day,
        resource_id=ctx.get("resource_id"), limit=MAX_OFFER,
    )
    if not slots:
        return await _go_waitlist(session, business, convo, ctx, day)
    offered = _offered_from_slots(slots)[:MAX_OFFER]
    ctx = {**ctx, "offered": offered}
    _set(convo, S.COLLECTING_DATETIME, ctx)
    return replies.offer_slots([o["label"] for o in offered])


def _slot_offer(slot: Any) -> dict[str, Any]:
    """Serializa un Slot al formato compacto que guardamos en el contexto."""
    return {
        "resource_id": slot.resource_id,
        "start_at": slot.start_at.isoformat(),
        "label": replies.fmt_slot(slot.start_at),
    }


def _strip_offers(ctx: dict[str, Any]) -> dict[str, Any]:
    """Quita las claves auxiliares de las ofertas escalonadas antes de confirmar."""
    return {k: v for k, v in ctx.items() if k not in ("alt", "nearest", "day")}


async def _request_specific_time(
    session, business, convo, ctx: dict[str, Any], day: date, want: str
) -> str:
    """El cliente pide una HORA concreta. Si su profesional no puede a esa hora,
    escalamos: 1) otro profesional libre a esa hora, 2) la hora más cercana con su
    profesional, 3) lista de espera."""
    hh, mm = (int(x) for x in want.split(":"))
    dt = timez.local(day, time(hh, mm))
    free = await free_resources_at(session, business.id, ctx["service_id"], dt)

    # Sin profesional fijo ("me da igual"): cualquiera libre a esa hora vale.
    if ctx.get("any_pro") or not ctx.get("resource_id"):
        if free:
            return await _choose_slot(session, business, convo, ctx, _slot_offer(free[0]))
        slots = await _find_slots(session, business.id, ctx["service_id"], day, None)
        if not slots:
            return await _go_waitlist(session, business, convo, ctx, day)
        ctx = {**ctx, "offered": _offered_from_slots(slots)}
        _set(convo, S.COLLECTING_DATETIME, ctx)
        return replies.offer_slots([o["label"] for o in ctx["offered"]])

    # Profesional concreto pedido.
    resource_id = ctx["resource_id"]
    mine = next((s for s in free if s.resource_id == resource_id), None)
    if mine is not None:  # su profesional sí está libre a esa hora.
        return await _choose_slot(session, business, convo, ctx, _slot_offer(mine))

    others = [s for s in free if s.resource_id != resource_id]
    if others:  # 1) recomendamos otro profesional libre a esa misma hora.
        alt = others[0]
        ctx2 = {**ctx, "alt": _slot_offer(alt), "day": day.isoformat(), "want": want}
        _set(convo, S.OFFER_ALT_PRO, ctx2)
        return replies.alt_professional(
            ctx.get("professional_name"), alt.resource_name,
            replies.fmt_slot(alt.start_at),
        )
    # 2) nadie más libre a esa hora → la hora más cercana con su profesional.
    return await _go_nearest(session, business, convo, ctx, day, target=dt)


def _target_dt(day_iso: str | None, want: str | None) -> datetime | None:
    """Reconstruye la hora pedida (para medir cercanía) desde el contexto."""
    if not day_iso or not want:
        return None
    hh, mm = (int(x) for x in want.split(":"))
    return timez.local(date.fromisoformat(day_iso), time(hh, mm))


async def _go_nearest(
    session, business, convo, ctx: dict[str, Any], from_day: date,
    target: datetime | None = None,
) -> str:
    """Ofrece el hueco más cercano con el profesional pedido (o lista de espera).

    Si conocemos la hora deseada, elegimos el hueco de ese día más próximo a ella;
    si ese día no queda ninguno, el primero disponible en los próximos días.
    """
    day_slots = await check_availability(
        session, business.id, ctx["service_id"], from_day, from_day,
        resource_id=ctx.get("resource_id"), limit=80,
    )
    if day_slots and target is not None:
        nearest = min(
            day_slots,
            key=lambda s: abs((timez.to_local(s.start_at) - target).total_seconds()),
        )
    elif day_slots:
        nearest = day_slots[0]
    else:
        ahead = await _find_slots(
            session, business.id, ctx["service_id"], from_day, ctx.get("resource_id")
        )
        if not ahead:
            return await _go_waitlist(session, business, convo, ctx, from_day)
        nearest = ahead[0]

    ctx2 = {**ctx, "nearest": _slot_offer(nearest), "day": from_day.isoformat()}
    _set(convo, S.OFFER_NEAREST, ctx2)
    return replies.nearest_with_pro(
        ctx.get("professional_name"), replies.fmt_slot(nearest.start_at)
    )


async def _go_waitlist(
    session, business, convo, ctx: dict[str, Any], day: date | None = None
) -> str:
    """Último recurso: ofrecer apuntar a la lista de espera."""
    wctx = {
        "service_id": ctx["service_id"],
        "service_name": ctx["service_name"],
        "resource_id": ctx.get("resource_id"),
        "any_pro": ctx.get("any_pro", False),
        "desired_date": day.isoformat() if day else ctx.get("desired_date"),
    }
    _set(convo, S.WAITLIST_OFFER, wctx)
    return replies.ask_waitlist(ctx["service_name"])


async def _offer_alt_pro(session, business, convo, intent, ctx: dict[str, Any]) -> str:
    """Respuesta a «¿te lo reservo con otro profesional a esa hora?»."""
    if intent == Intent.CONFIRM.value:
        return await _choose_slot(
            session, business, convo, _strip_offers(ctx), dict(ctx["alt"])
        )
    if intent == Intent.DENY.value:  # no quiere otro pro → su hora más cercana.
        return await _go_nearest(
            session, business, convo, _strip_offers(ctx),
            date.fromisoformat(ctx["day"]),
            target=_target_dt(ctx.get("day"), ctx.get("want")),
        )
    return replies.confirm_yes_no()


async def _offer_nearest(session, business, convo, intent, ctx: dict[str, Any]) -> str:
    """Respuesta a «¿te vale la hora más cercana con tu profesional?»."""
    if intent == Intent.CONFIRM.value:
        return await _choose_slot(
            session, business, convo, _strip_offers(ctx), dict(ctx["nearest"])
        )
    if intent == Intent.DENY.value:  # tampoco → lista de espera.
        return await _go_waitlist(
            session, business, convo, _strip_offers(ctx), date.fromisoformat(ctx["day"])
        )
    return replies.confirm_yes_no()


def _collecting_contact(convo, ext, ctx) -> str:
    name = ext.data.get("name") or None
    if not name:
        return replies.ask_name()
    ctx = {**ctx, "name": name, "action": "book"}
    _set(convo, S.CONFIRMING, ctx)
    return replies.confirm_booking(ctx["service_name"], ctx["chosen"]["label"])


async def _start_manage(session, business, convo, phone, intent, professionals) -> str:
    appts = await _upcoming_appointments(session, business.id, phone)
    if not appts:
        _set(convo, S.IDLE, {})
        return replies.no_appointments()
    appt = appts[0]
    service = await session.get(Service, appt.service_id)
    name = service.name if service else "tu cita"
    when = replies.fmt_slot(appt.start_at)

    if intent == Intent.CANCEL.value:
        ctx = {"action": "cancel", "appt_id": appt.id, "service_name": name, "when": when}
        _set(convo, S.CONFIRMING, ctx)
        return replies.confirm_cancel(name, when)

    # Reprogramar: como una reserva del mismo servicio. Se puede mantener o
    # cambiar el profesional (o "me da igual") y luego elegir nueva hora.
    cur = await session.get(Resource, appt.resource_id)
    ctx = {
        "mode": "reschedule",
        "appt_id": appt.id,
        "service_id": appt.service_id,
        "service_name": name,
    }
    _set(convo, S.COLLECTING_PROFESSIONAL, ctx)
    return replies.reschedule_ask_professional(
        name, when, cur.name if cur else None, [p.name for p in professionals]
    )


async def _waitlist_offer(session, business, convo, phone, intent, ctx) -> str:
    """Tras 'sin huecos': si dice que sí, lo apuntamos a la lista de espera."""
    if intent != Intent.CONFIRM.value:
        _set(convo, S.IDLE, {})
        return replies.aborted()
    day = None
    raw = ctx.get("desired_date")
    if raw:
        try:
            day = date.fromisoformat(raw)
        except ValueError:
            day = None
    resource_id = None if ctx.get("any_pro") else ctx.get("resource_id")
    name = await _customer_name(session, business.id, phone)
    await add_to_waitlist(
        session,
        business_id=business.id,
        phone=phone,
        service_id=ctx["service_id"],
        name=name,
        resource_id=resource_id,
        desired_date=day,
    )
    _set(convo, S.IDLE, {})
    return replies.waitlist_added(ctx["service_name"])


async def _confirming(session, business, convo, phone, intent, ctx, message_id) -> str:
    if intent == Intent.DENY.value:
        _set(convo, S.IDLE, {})
        return replies.aborted()
    if intent not in (Intent.CONFIRM.value, Intent.CHOOSE.value):
        return "¿Te lo confirmo? Responde sí o no."

    action = ctx.get("action")
    try:
        if action == "book":
            chosen = ctx["chosen"]
            await book_appointment(
                session,
                business_id=business.id,
                service_id=ctx["service_id"],
                start_at=datetime.fromisoformat(chosen["start_at"]),
                phone=phone,
                name=ctx.get("name"),
                resource_id=chosen.get("resource_id"),
                idempotency_key=message_id,
                # Toda cita nace PENDIENTE; la asistencia se marca tras la hora.
                status=AppointmentStatus.PENDING,
            )
            _set(convo, S.IDLE, {})
            return replies.booking_done(
                ctx["service_name"], chosen["label"], ctx.get("name")
            )

        if action == "reschedule":
            chosen = ctx["chosen"]
            await reschedule_appointment(
                session, business.id, ctx["appt_id"],
                datetime.fromisoformat(chosen["start_at"]),
                new_resource_id=chosen.get("resource_id"),
            )
            _set(convo, S.IDLE, {})
            return replies.reschedule_done(ctx["service_name"], chosen["label"])

        if action == "cancel":
            await cancel_appointment(session, business.id, ctx["appt_id"])
            _set(convo, S.IDLE, {})
            return replies.cancel_done()

    except SlotTakenError:
        # El hueco se ocupó entre la oferta y la confirmación: re-ofertamos.
        keep = ("mode", "service_id", "service_name", "resource_id",
                "any_pro", "professional_name", "appt_id")
        retry = {k: ctx[k] for k in keep if k in ctx}
        _set(convo, S.COLLECTING_DATETIME, retry)
        return replies.slot_taken()
    except BookingError as exc:
        _set(convo, S.IDLE, {})
        logger.info("Reserva rechazada: %s", exc)
        return f"No he podido completar la cita: {exc}. ¿Probamos de nuevo?"

    _set(convo, S.IDLE, {})
    return replies.fallback()
