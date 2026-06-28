"""API de administración: alta de negocios, servicios, recursos y reservas.

Esta es la consola de back-office (futuro panel multi-tenant). El agente de
WhatsApp usa la misma capa de tools por debajo.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import invalidate_context
from app.audit import verify_chain
from app.database import get_session
from app.models import (
    Appointment,
    AuditLog,
    Business,
    BusinessClosure,
    Conversation,
    ConversationState,
    Resource,
    Service,
    TimeOff,
    WaitlistEntry,
)
from app.schemas.appointment import (
    AppointmentOut,
    AvailabilityRequest,
    BookingRequest,
    PriceInfo,
    RescheduleRequest,
    Slot,
    StatusUpdate,
)
from app.schemas.audit import AuditOut, AuditVerifyOut
from app.schemas.business import (
    BusinessCreate,
    BusinessOut,
    BusinessUpdate,
    ResourceCreate,
    ResourceOut,
)
from app.schemas.conversation import ConversationOut
from app.schemas.schedule import (
    ClosureCreate,
    ClosureOut,
    DayInfo,
    TimeOffCreate,
    TimeOffOut,
)
from app.schemas.service import (
    ServiceCreate,
    ServiceOut,
    ServiceResourcesIn,
    ServiceUpdate,
)
from app.schemas.waitlist import WaitlistCreate, WaitlistOut
from app.tools import (
    BookingError,
    add_to_waitlist,
    assign_service_resources,
    book_appointment,
    cancel_appointment,
    check_availability,
    get_pricing,
    list_service_resources,
    list_waitlist,
    remove_from_waitlist,
    reschedule_appointment,
)
from app.tools.scheduling import business_day_intervals

router = APIRouter(prefix="/admin", tags=["admin"])


async def _get_business(session: AsyncSession, business_id: str) -> Business:
    business = await session.get(Business, business_id)
    if business is None:
        raise HTTPException(404, "Negocio no encontrado")
    return business


# --------------------------------------------------------------------------- #
#  Negocios
# --------------------------------------------------------------------------- #
@router.post("/businesses", response_model=BusinessOut, status_code=201)
async def create_business(
    payload: BusinessCreate, session: AsyncSession = Depends(get_session)
) -> Business:
    business = Business(**payload.model_dump())
    session.add(business)
    await session.flush()
    return business


@router.get("/businesses", response_model=list[BusinessOut])
async def list_businesses(session: AsyncSession = Depends(get_session)) -> list[Business]:
    return list((await session.scalars(select(Business))).all())


@router.get("/businesses/{business_id}", response_model=BusinessOut)
async def get_business(
    business_id: str, session: AsyncSession = Depends(get_session)
) -> Business:
    return await _get_business(session, business_id)


@router.patch("/businesses/{business_id}", response_model=BusinessOut)
async def update_business(
    business_id: str,
    payload: BusinessUpdate,
    session: AsyncSession = Depends(get_session),
) -> Business:
    """Edita el horario de apertura, granularidad y datos del negocio.

    El horario aquí guardado es la fuente de verdad: el agente de WhatsApp lo lee
    al comprobar si el negocio está abierto ese día y a esa hora.
    """
    business = await _get_business(session, business_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(business, field, value)
    await session.flush()
    invalidate_context(business_id)  # el horario cacheado pudo cambiar
    return business


@router.get("/businesses/{business_id}/day-info", response_model=DayInfo)
async def day_info(
    business_id: str,
    day: date = Query(..., alias="date"),
    session: AsyncSession = Depends(get_session),
) -> DayInfo:
    """Horario efectivo de una fecha: abierto/cerrado, tramos y si es continuo o partido.

    Cruza el horario semanal con los cierres/aperturas especiales usando la MISMA
    función que la reserva, de modo que el panel muestra exactamente lo que el
    agente aplicará al agendar.
    """
    business = await _get_business(session, business_id)
    closure = (
        await session.scalars(
            select(BusinessClosure).where(
                BusinessClosure.business_id == business_id,
                BusinessClosure.date == day,
            )
        )
    ).first()
    intervals = business_day_intervals(business, closure, day)
    kind = "cerrado" if not intervals else "continuo" if len(intervals) == 1 else "partido"
    return DayInfo(
        date=day,
        weekday=day.weekday(),
        is_open=bool(intervals),
        kind=kind,
        intervals=[[a.strftime("%H:%M"), b.strftime("%H:%M")] for a, b in intervals],
        is_special=closure is not None,
        reason=closure.reason if closure is not None else None,
    )


# --------------------------------------------------------------------------- #
#  Auditoría (append-only, encadenada por hash)
# --------------------------------------------------------------------------- #
@router.get("/audit", response_model=list[AuditOut])
async def list_audit(
    action: Literal["mutation", "security"] | None = Query(
        None, description="Filtra por acción: mutation|security"
    ),
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> list[AuditLog]:
    query = select(AuditLog).order_by(AuditLog.seq.desc()).limit(limit)
    if action:
        query = query.where(AuditLog.action == action)
    return list((await session.scalars(query)).all())


@router.get("/audit/verify", response_model=AuditVerifyOut)
async def verify_audit(
    session: AsyncSession = Depends(get_session),
) -> AuditVerifyOut:
    """Recomputa la cadena de hashes; `ok=false` si algo se alteró o borró."""
    return AuditVerifyOut(**await verify_chain(session))


# --------------------------------------------------------------------------- #
#  Servicios (nombre · duración · precio)
# --------------------------------------------------------------------------- #
@router.post(
    "/businesses/{business_id}/services", response_model=ServiceOut, status_code=201
)
async def create_service(
    business_id: str,
    payload: ServiceCreate,
    session: AsyncSession = Depends(get_session),
) -> Service:
    await _get_business(session, business_id)
    service = Service(business_id=business_id, **payload.model_dump())
    session.add(service)
    await session.flush()
    invalidate_context(business_id)  # el catálogo cacheado cambió
    return service


@router.get(
    "/businesses/{business_id}/services", response_model=list[ServiceOut]
)
async def list_business_services(
    business_id: str,
    include_inactive: bool = Query(False),
    session: AsyncSession = Depends(get_session),
) -> list[Service]:
    query = select(Service).where(Service.business_id == business_id)
    if not include_inactive:
        query = query.where(Service.active.is_(True))
    return list((await session.scalars(query.order_by(Service.name))).all())


@router.patch(
    "/businesses/{business_id}/services/{service_id}", response_model=ServiceOut
)
async def update_service(
    business_id: str,
    service_id: str,
    payload: ServiceUpdate,
    session: AsyncSession = Depends(get_session),
) -> Service:
    service = await session.get(Service, service_id)
    if service is None or service.business_id != business_id:
        raise HTTPException(404, "Servicio no encontrado")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(service, field, value)
    await session.flush()
    invalidate_context(business_id)
    return service


@router.delete(
    "/businesses/{business_id}/services/{service_id}", status_code=204
)
async def delete_service(
    business_id: str,
    service_id: str,
    session: AsyncSession = Depends(get_session),
) -> None:
    service = await session.get(Service, service_id)
    if service is None or service.business_id != business_id:
        raise HTTPException(404, "Servicio no encontrado")
    # Borrado lógico para no romper el histórico de citas.
    service.active = False
    await session.flush()
    invalidate_context(business_id)


@router.get(
    "/businesses/{business_id}/pricing", response_model=list[PriceInfo]
)
async def pricing(
    business_id: str, session: AsyncSession = Depends(get_session)
) -> list[PriceInfo]:
    await _get_business(session, business_id)
    return await get_pricing(session, business_id)


# --------------------------------------------------------------------------- #
#  Recursos
# --------------------------------------------------------------------------- #
@router.post(
    "/businesses/{business_id}/resources", response_model=ResourceOut, status_code=201
)
async def create_resource(
    business_id: str,
    payload: ResourceCreate,
    session: AsyncSession = Depends(get_session),
) -> Resource:
    await _get_business(session, business_id)
    resource = Resource(business_id=business_id, **payload.model_dump())
    session.add(resource)
    await session.flush()
    return resource


@router.get(
    "/businesses/{business_id}/resources", response_model=list[ResourceOut]
)
async def list_resources(
    business_id: str, session: AsyncSession = Depends(get_session)
) -> list[Resource]:
    return list(
        (
            await session.scalars(
                select(Resource).where(Resource.business_id == business_id)
            )
        ).all()
    )


# --------------------------------------------------------------------------- #
#  Capacidad servicio↔recurso (qué profesional hace qué servicio)
# --------------------------------------------------------------------------- #
@router.get(
    "/businesses/{business_id}/services/{service_id}/resources",
    response_model=list[str],
)
async def get_service_resources(
    business_id: str,
    service_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[str]:
    try:
        return await list_service_resources(session, business_id, service_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.put(
    "/businesses/{business_id}/services/{service_id}/resources",
    response_model=list[str],
)
async def put_service_resources(
    business_id: str,
    service_id: str,
    payload: ServiceResourcesIn,
    session: AsyncSession = Depends(get_session),
) -> list[str]:
    try:
        return await assign_service_resources(
            session, business_id, service_id, payload.resource_ids
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# --------------------------------------------------------------------------- #
#  Festivos / cierres del negocio
# --------------------------------------------------------------------------- #
@router.post(
    "/businesses/{business_id}/closures", response_model=ClosureOut, status_code=201
)
async def create_closure(
    business_id: str,
    payload: ClosureCreate,
    session: AsyncSession = Depends(get_session),
) -> BusinessClosure:
    await _get_business(session, business_id)
    closure = BusinessClosure(business_id=business_id, **payload.model_dump())
    session.add(closure)
    await session.flush()
    return closure


@router.get(
    "/businesses/{business_id}/closures", response_model=list[ClosureOut]
)
async def list_closures(
    business_id: str, session: AsyncSession = Depends(get_session)
) -> list[BusinessClosure]:
    return list(
        (
            await session.scalars(
                select(BusinessClosure)
                .where(BusinessClosure.business_id == business_id)
                .order_by(BusinessClosure.date)
            )
        ).all()
    )


@router.delete(
    "/businesses/{business_id}/closures/{closure_id}", status_code=204
)
async def delete_closure(
    business_id: str,
    closure_id: str,
    session: AsyncSession = Depends(get_session),
) -> None:
    closure = await session.get(BusinessClosure, closure_id)
    if closure is None or closure.business_id != business_id:
        raise HTTPException(404, "Cierre no encontrado")
    await session.delete(closure)


# --------------------------------------------------------------------------- #
#  Ausencias de recursos (días libres, vacaciones, bajas)
# --------------------------------------------------------------------------- #
@router.post(
    "/businesses/{business_id}/time-off", response_model=TimeOffOut, status_code=201
)
async def create_time_off(
    business_id: str,
    payload: TimeOffCreate,
    session: AsyncSession = Depends(get_session),
) -> TimeOff:
    await _get_business(session, business_id)
    resource = await session.get(Resource, payload.resource_id)
    if resource is None or resource.business_id != business_id:
        raise HTTPException(404, "Recurso no encontrado")
    off = TimeOff(business_id=business_id, **payload.model_dump())
    session.add(off)
    await session.flush()
    return off


@router.get(
    "/businesses/{business_id}/time-off", response_model=list[TimeOffOut]
)
async def list_time_off(
    business_id: str, session: AsyncSession = Depends(get_session)
) -> list[TimeOff]:
    return list(
        (
            await session.scalars(
                select(TimeOff)
                .where(TimeOff.business_id == business_id)
                .order_by(TimeOff.start_at)
            )
        ).all()
    )


@router.delete(
    "/businesses/{business_id}/time-off/{time_off_id}", status_code=204
)
async def delete_time_off(
    business_id: str,
    time_off_id: str,
    session: AsyncSession = Depends(get_session),
) -> None:
    off = await session.get(TimeOff, time_off_id)
    if off is None or off.business_id != business_id:
        raise HTTPException(404, "Ausencia no encontrada")
    await session.delete(off)


# --------------------------------------------------------------------------- #
#  Lista de espera (relleno automático de cancelaciones)
# --------------------------------------------------------------------------- #
@router.get(
    "/businesses/{business_id}/waitlist", response_model=list[WaitlistOut]
)
async def get_waitlist(
    business_id: str,
    only_waiting: bool = Query(True),
    session: AsyncSession = Depends(get_session),
) -> list[WaitlistEntry]:
    return await list_waitlist(session, business_id, only_waiting=only_waiting)


@router.post(
    "/businesses/{business_id}/waitlist", response_model=WaitlistOut, status_code=201
)
async def create_waitlist_entry(
    business_id: str,
    payload: WaitlistCreate,
    session: AsyncSession = Depends(get_session),
) -> WaitlistEntry:
    await _get_business(session, business_id)
    service = await session.get(Service, payload.service_id)
    if service is None or service.business_id != business_id:
        raise HTTPException(404, "Servicio no encontrado")
    return await add_to_waitlist(
        session,
        business_id=business_id,
        phone=payload.customer.phone,
        name=payload.customer.name,
        service_id=payload.service_id,
        resource_id=payload.resource_id,
        desired_date=payload.desired_date,
    )


@router.delete(
    "/businesses/{business_id}/waitlist/{entry_id}", status_code=204
)
async def delete_waitlist_entry(
    business_id: str,
    entry_id: str,
    session: AsyncSession = Depends(get_session),
) -> None:
    if not await remove_from_waitlist(session, business_id, entry_id):
        raise HTTPException(404, "Entrada de lista de espera no encontrada")


# --------------------------------------------------------------------------- #
#  Bandeja de handoff: conversaciones que requieren a una persona
# --------------------------------------------------------------------------- #
@router.get(
    "/businesses/{business_id}/handoffs", response_model=list[ConversationOut]
)
async def list_handoffs(
    business_id: str, session: AsyncSession = Depends(get_session)
) -> list[Conversation]:
    return list(
        (
            await session.scalars(
                select(Conversation)
                .where(
                    Conversation.business_id == business_id,
                    Conversation.state == ConversationState.HUMAN_HANDOFF,
                )
                .order_by(Conversation.updated_at)
            )
        ).all()
    )


@router.post(
    "/businesses/{business_id}/conversations/{conversation_id}/release",
    response_model=ConversationOut,
)
async def release_conversation(
    business_id: str,
    conversation_id: str,
    session: AsyncSession = Depends(get_session),
) -> Conversation:
    """Devuelve la conversación al bot (estado IDLE) tras atenderla una persona."""
    convo = await session.get(Conversation, conversation_id)
    if convo is None or convo.business_id != business_id:
        raise HTTPException(404, "Conversación no encontrada")
    convo.state = ConversationState.IDLE
    convo.context = {}
    await session.flush()
    return convo


# --------------------------------------------------------------------------- #
#  Disponibilidad y reservas (mismas tools que usa el agente)
# --------------------------------------------------------------------------- #
@router.post(
    "/businesses/{business_id}/availability", response_model=list[Slot]
)
async def availability(
    business_id: str,
    payload: AvailabilityRequest,
    session: AsyncSession = Depends(get_session),
) -> list[Slot]:
    try:
        return await check_availability(
            session,
            business_id=business_id,
            service_id=payload.service_id,
            date_from=payload.date_from,
            date_to=payload.date_to,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post(
    "/businesses/{business_id}/appointments",
    response_model=AppointmentOut,
    status_code=201,
)
async def create_appointment(
    business_id: str,
    payload: BookingRequest,
    session: AsyncSession = Depends(get_session),
) -> AppointmentOut:
    try:
        appt = await book_appointment(
            session,
            business_id=business_id,
            service_id=payload.service_id,
            start_at=payload.start_at,
            phone=payload.customer.phone,
            name=payload.customer.name,
            resource_id=payload.resource_id,
            idempotency_key=payload.idempotency_key,
            notes=payload.notes,
            force=payload.force,
        )
    except BookingError as exc:
        raise HTTPException(409, str(exc)) from exc
    return AppointmentOut.model_validate(appt)


@router.post(
    "/businesses/{business_id}/appointments/{appointment_id}/cancel",
    status_code=200,
)
async def cancel(
    business_id: str,
    appointment_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    try:
        ok = await cancel_appointment(session, business_id, appointment_id)
    except BookingError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"cancelled": ok}


@router.post(
    "/businesses/{business_id}/appointments/{appointment_id}/reschedule",
    response_model=AppointmentOut,
)
async def reschedule(
    business_id: str,
    appointment_id: str,
    payload: RescheduleRequest,
    session: AsyncSession = Depends(get_session),
) -> AppointmentOut:
    try:
        appt = await reschedule_appointment(
            session, business_id, appointment_id, payload.new_start_at
        )
    except BookingError as exc:
        raise HTTPException(409, str(exc)) from exc
    return AppointmentOut.model_validate(appt)


@router.post(
    "/businesses/{business_id}/appointments/{appointment_id}/status",
    response_model=AppointmentOut,
)
async def set_status(
    business_id: str,
    appointment_id: str,
    payload: StatusUpdate,
    session: AsyncSession = Depends(get_session),
) -> AppointmentOut:
    """Marca el estado desde el back-office (confirmar, completar, no-show…)."""
    appt = await session.get(Appointment, appointment_id)
    if appt is None or appt.business_id != business_id:
        raise HTTPException(404, "Cita no encontrada")
    appt.status = payload.status
    await session.flush()
    return AppointmentOut.model_validate(appt)
