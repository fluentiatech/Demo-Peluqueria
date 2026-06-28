"""Tool: reservar, cancelar y reprogramar citas.

Garantías:
  - Idempotencia: una reentrega del webhook de Meta (mismo idempotency_key)
    no crea una segunda cita.
  - Anti-doble-reserva: dos clientes pidiendo el mismo recurso a la vez no
    pueden ambos confirmar. Se apoya en la BD, no en el LLM.

Reglas de calendario (compartidas con la tool de disponibilidad):
  - solo recursos cualificados para el servicio,
  - dentro del horario efectivo (negocio ∩ recurso, con festivos/cierres),
  - respetando ausencias del recurso y los buffers de preparación/limpieza.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import timez
from app.config import settings
from app.models import (
    Appointment,
    AppointmentStatus,
    Business,
    BusinessClosure,
    Customer,
    EventLog,
    Resource,
    Service,
    TimeOff,
)
from app.models.base import utcnow
from app.tools.scheduling import (
    business_day_intervals,
    eligible_resource_ids,
    resource_day_intervals,
)

_BLOCKING_STATUSES = (
    AppointmentStatus.PENDING,
    AppointmentStatus.CONFIRMED,
    AppointmentStatus.COMPLETED,
)


def _emit_slot_freed(session: AsyncSession, appt: Appointment) -> None:
    """Registra que un hueco quedó libre, para el relleno de la lista de espera.

    Desacoplado: un cron (`scripts.fill_waitlist`) procesa estos eventos y ofrece
    el hueco al primero en espera. No bloquea la cancelación ni envía nada aquí.
    """
    session.add(
        EventLog(
            business_id=appt.business_id,
            type="slot_freed",
            payload={
                "service_id": appt.service_id,
                "resource_id": appt.resource_id,
                "start_at": appt.start_at.isoformat(),
            },
        )
    )


class BookingError(Exception):
    """Error de negocio al reservar (datos inválidos, fuera de horario, etc.)."""


class SlotTakenError(BookingError):
    """El hueco solicitado ya está ocupado."""


class OutOfHoursError(BookingError):
    """El horario solicitado cae fuera de la apertura o en un día cerrado."""


async def _resource_lock(session: AsyncSession, resource_id: str) -> None:
    """Serializa las reservas sobre un mismo recurso bajo concurrencia.

    En Postgres usa un advisory lock transaccional; en SQLite (desarrollo) el
    acceso ya está serializado por el propio motor, así que es un no-op.
    """
    if settings.is_sqlite:
        return
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:rid))"), {"rid": resource_id}
    )


async def _get_or_create_customer(
    session: AsyncSession, business_id: str, phone: str, name: str | None
) -> Customer:
    customer = await session.scalar(
        select(Customer).where(
            Customer.business_id == business_id, Customer.phone == phone
        )
    )
    if customer is None:
        # RGPD: registramos el momento del consentimiento implícito (el cliente
        # inicia el contacto/reserva). El responsable debe informar de la política.
        customer = Customer(
            business_id=business_id, phone=phone, name=name, consent_at=utcnow()
        )
        session.add(customer)
        await session.flush()
    elif name and not customer.name:
        customer.name = name
    return customer


async def _closure_for(
    session: AsyncSession, business_id: str, day
) -> BusinessClosure | None:
    return await session.scalar(
        select(BusinessClosure).where(
            BusinessClosure.business_id == business_id,
            BusinessClosure.date == day,
        )
    )


async def _has_appt_conflict(
    session: AsyncSession,
    resource_id: str,
    block_start: datetime,
    block_end: datetime,
    exclude_id: str | None = None,
) -> bool:
    query = select(Appointment).where(
        Appointment.resource_id == resource_id,
        Appointment.status.in_(_BLOCKING_STATUSES),
        Appointment.block_start_at < block_end,
        Appointment.block_end_at > block_start,
    )
    if exclude_id:
        query = query.where(Appointment.id != exclude_id)
    return (await session.scalar(query.limit(1))) is not None


async def _day_appointment_counts(
    session: AsyncSession, business_id: str, day: date
) -> dict[str, int]:
    """Citas activas por recurso en un día (para el reparto de carga)."""
    start = timez.local(day, time.min)
    end = start + timedelta(days=1)
    rows = (
        await session.execute(
            select(Appointment.resource_id, func.count())
            .where(
                Appointment.business_id == business_id,
                Appointment.status.in_(_BLOCKING_STATUSES),
                Appointment.start_at >= start,
                Appointment.start_at < end,
            )
            .group_by(Appointment.resource_id)
        )
    ).all()
    return {rid: int(c) for rid, c in rows}


async def _has_time_off(
    session: AsyncSession, resource_id: str, start: datetime, end: datetime
) -> bool:
    off = await session.scalar(
        select(TimeOff)
        .where(
            TimeOff.resource_id == resource_id,
            TimeOff.start_at < end,
            TimeOff.end_at > start,
        )
        .limit(1)
    )
    return off is not None


async def book_appointment(
    session: AsyncSession,
    business_id: str,
    service_id: str,
    start_at: datetime,
    phone: str,
    name: str | None = None,
    resource_id: str | None = None,
    idempotency_key: str | None = None,
    notes: str | None = None,
    status: AppointmentStatus = AppointmentStatus.PENDING,
    force: bool = False,
) -> Appointment:
    """Crea una cita de forma idempotente, sin solapes y dentro de horario.

    `status` permite crearla ya CONFIRMED (p. ej. cuando el cliente confirma por
    chat). `force=True` (alta manual del back-office) **salta** la validación de
    horario/cierre/ausencia, pero NUNCA el anti-doble-reserva.
    """
    # Toda hora de cita se maneja en la zona del negocio (España). Si llega sin
    # zona (naive), la interpretamos como hora local; así "las 9" son las 9 aquí.
    start_at = timez.aware(start_at)

    # 1) Idempotencia: si ya procesamos este mensaje, devolvemos la cita creada.
    if idempotency_key:
        existing = await session.scalar(
            select(Appointment).where(
                Appointment.business_id == business_id,
                Appointment.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing

    business = await session.get(Business, business_id)
    if business is None:
        raise BookingError("Negocio no encontrado")

    service = await session.get(Service, service_id)
    if service is None or service.business_id != business_id:
        raise BookingError("Servicio no encontrado")
    if not service.active:
        raise BookingError("El servicio no está disponible")

    if start_at < timez.now():
        raise BookingError("No se puede reservar en el pasado")

    end_at = start_at + timedelta(minutes=service.duration_min)
    block_start = start_at - timedelta(minutes=service.buffer_before_min)
    block_end = end_at + timedelta(minutes=service.buffer_after_min)

    # 2) Horario del negocio ese día (festivos/cierres incluidos).
    day = start_at.date()
    closure = await _closure_for(session, business_id, day)
    biz_intervals = business_day_intervals(business, closure, day)
    if not biz_intervals and not force:
        raise OutOfHoursError("El negocio está cerrado ese día")

    # 3) Recursos candidatos (activos, cualificados, opcionalmente el pedido).
    eligible = await eligible_resource_ids(session, service_id)
    cand_query = select(Resource).where(
        Resource.business_id == business_id, Resource.active.is_(True)
    )
    if resource_id:
        cand_query = cand_query.where(Resource.id == resource_id)
    if eligible is not None:
        cand_query = cand_query.where(Resource.id.in_(eligible))
    candidates = (await session.scalars(cand_query)).all()
    if not candidates:
        raise BookingError("No hay recursos cualificados para este servicio")

    # Recursos realmente libres para el hueco (horario + sin conflicto + sin ausencia).
    free: list[Resource] = []
    fits_hours = False
    for res in candidates:
        if not force:
            res_intervals = resource_day_intervals(biz_intervals, res, day)
            if not _within(res_intervals, day, start_at, end_at):
                continue
        fits_hours = True
        await _resource_lock(session, res.id)
        if await _has_appt_conflict(session, res.id, block_start, block_end):
            continue
        if not force and await _has_time_off(session, res.id, block_start, block_end):
            continue
        free.append(res)

    if not free:
        if not fits_hours:
            raise OutOfHoursError("El horario solicitado está fuera de la apertura")
        raise SlotTakenError("El hueco solicitado ya no está disponible")

    if resource_id or len(free) == 1:
        chosen = free[0]
    else:
        # "Me da igual": reparto de carga → el profesional con MENOS citas ese día.
        counts = await _day_appointment_counts(session, business_id, day)
        chosen = min(free, key=lambda r: counts.get(r.id, 0))

    customer = await _get_or_create_customer(session, business_id, phone, name)

    appt = Appointment(
        business_id=business_id,
        service_id=service_id,
        resource_id=chosen.id,
        customer_id=customer.id,
        start_at=start_at,
        end_at=end_at,
        block_start_at=block_start,
        block_end_at=block_end,
        status=status,
        # Snapshot del servicio para histórico fiable.
        service_name=service.name,
        price=service.price,
        duration_min=service.duration_min,
        idempotency_key=idempotency_key,
        notes=notes,
    )
    session.add(appt)
    try:
        await session.flush()  # dispara el UNIQUE(resource_id, start_at)
    except IntegrityError as exc:
        await session.rollback()
        raise SlotTakenError("El hueco solicitado ya no está disponible") from exc

    return appt


def _within(intervals, day, start: datetime, end: datetime) -> bool:
    """¿Cabe [start, end] en algún tramo abierto del día?"""
    for i0, i1 in intervals:
        if timez.local(day, i0) <= start and end <= timez.local(day, i1):
            return True
    return False


async def cancel_appointment(
    session: AsyncSession, business_id: str, appointment_id: str
) -> bool:
    """Cancela una cita ELIMINÁNDOLA (libera el hueco). No deja estado 'cancelada'."""
    appt = await session.get(Appointment, appointment_id)
    if appt is None or appt.business_id != business_id:
        raise BookingError("Cita no encontrada")
    _emit_slot_freed(session, appt)  # alimenta el relleno de la lista de espera
    await session.delete(appt)
    await session.flush()
    return True


async def reschedule_appointment(
    session: AsyncSession,
    business_id: str,
    appointment_id: str,
    new_start_at: datetime,
    new_resource_id: str | None = None,
) -> Appointment:
    """Mueve la cita a una nueva hora y, opcionalmente, a otro profesional.

    Si `new_resource_id` se indica y es distinto, valida que ese profesional esté
    activo y cualificado para el servicio, y traslada la cita a él.
    """
    new_start_at = timez.aware(new_start_at)
    appt = await session.get(Appointment, appointment_id)
    if appt is None or appt.business_id != business_id:
        raise BookingError("Cita no encontrada")
    if appt.status == AppointmentStatus.COMPLETED:
        raise BookingError("La cita no se puede reprogramar")
    if new_start_at < timez.now():
        raise BookingError("No se puede reprogramar al pasado")

    business = await session.get(Business, business_id)
    service = await session.get(Service, appt.service_id)
    if business is None or service is None:
        raise BookingError("Datos de la cita inconsistentes")

    target_id = new_resource_id or appt.resource_id
    resource = await session.get(Resource, target_id)
    if resource is None or resource.business_id != business_id or not resource.active:
        raise BookingError("Profesional no disponible")
    # Al cambiar de profesional, debe poder hacer ese servicio.
    if target_id != appt.resource_id:
        eligible = await eligible_resource_ids(session, appt.service_id)
        if eligible is not None and target_id not in eligible:
            raise BookingError("Ese profesional no realiza este servicio")

    new_end = new_start_at + timedelta(minutes=service.duration_min)
    block_start = new_start_at - timedelta(minutes=service.buffer_before_min)
    block_end = new_end + timedelta(minutes=service.buffer_after_min)

    day = new_start_at.date()
    closure = await _closure_for(session, business_id, day)
    biz_intervals = business_day_intervals(business, closure, day)
    if not biz_intervals:
        raise OutOfHoursError("El negocio está cerrado ese día")

    res_intervals = resource_day_intervals(biz_intervals, resource, day)
    if not _within(res_intervals, day, new_start_at, new_end):
        raise OutOfHoursError("El nuevo horario está fuera de la apertura")

    await _resource_lock(session, target_id)
    if await _has_appt_conflict(
        session, target_id, block_start, block_end, exclude_id=appt.id
    ):
        raise SlotTakenError("El nuevo hueco ya está ocupado")
    if await _has_time_off(session, target_id, block_start, block_end):
        raise OutOfHoursError("El recurso no está disponible en ese horario")

    _emit_slot_freed(session, appt)  # el hueco antiguo queda libre → lista de espera
    appt.resource_id = target_id
    appt.start_at = new_start_at
    appt.end_at = new_end
    appt.block_start_at = block_start
    appt.block_end_at = block_end
    await session.flush()
    return appt
