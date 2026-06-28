"""Consultas agregadas para el panel de gestión.

Todo en SQL parametrizado (sin construir cadenas), agregando en la BD cuando se
puede para que escale. La facturación cuenta como ingreso lo **completado**
(asistió); lo confirmado/pendiente aún por venir es "previsto" y los no-shows son
ingreso "perdido".
"""
from __future__ import annotations

from datetime import date, time, timedelta
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import timez
from app.models import Appointment, AppointmentStatus, Customer, Resource
from app.schemas.reports import (
    AgendaItem,
    AgendaOut,
    AgendaResource,
    BillingBucket,
    BillingOut,
    CustomerDetail,
    CustomerStat,
    StatusCount,
)

_St = AppointmentStatus


def _item(appt: Appointment, cust_name, cust_phone, res_name) -> AgendaItem:
    return AgendaItem(
        id=appt.id,
        resource_id=appt.resource_id,
        resource_name=res_name,
        service_name=appt.service_name,
        price=appt.price,
        customer_id=appt.customer_id,
        customer_name=cust_name,
        customer_phone=cust_phone,
        start_at=appt.start_at,
        end_at=appt.end_at,
        status=appt.status,
    )


def _appt_join(business_id: str):
    return (
        select(
            Appointment,
            Customer.name.label("cust_name"),
            Customer.phone.label("cust_phone"),
            Resource.name.label("res_name"),
        )
        .join(Customer, Appointment.customer_id == Customer.id)
        .join(Resource, Appointment.resource_id == Resource.id)
        .where(Appointment.business_id == business_id)
    )


async def agenda(session: AsyncSession, business_id: str, day: date) -> AgendaOut:
    start = timez.local(day, time.min)
    end = start + timedelta(days=1)
    rows = (
        await session.execute(
            _appt_join(business_id)
            .where(Appointment.start_at >= start, Appointment.start_at < end)
            .order_by(Resource.name, Appointment.start_at)
        )
    ).all()
    items = [_item(r.Appointment, r.cust_name, r.cust_phone, r.res_name) for r in rows]

    resources = (
        await session.scalars(
            select(Resource)
            .where(Resource.business_id == business_id, Resource.active.is_(True))
            .order_by(Resource.name)
        )
    ).all()
    return AgendaOut(
        date=day,
        resources=[AgendaResource(id=r.id, name=r.name) for r in resources],
        items=items,
    )


def _customer_stat_select(business_id: str):
    completed = func.sum(case((Appointment.status == _St.COMPLETED, 1), else_=0))
    no_shows = func.sum(case((Appointment.status == _St.NO_SHOW, 1), else_=0))
    spent = func.coalesce(
        func.sum(case((Appointment.status == _St.COMPLETED, Appointment.price), else_=0)),
        0,
    )
    return (
        select(
            Customer.id,
            Customer.name,
            Customer.phone,
            func.count(Appointment.id).label("total"),
            completed.label("completed"),
            no_shows.label("no_shows"),
            spent.label("spent"),
            func.max(Appointment.start_at).label("last_visit"),
        )
        .outerjoin(Appointment, Appointment.customer_id == Customer.id)
        .where(Customer.business_id == business_id)
        .group_by(Customer.id, Customer.name, Customer.phone)
    )


def _row_to_stat(r) -> CustomerStat:
    return CustomerStat(
        id=r.id,
        name=r.name,
        phone=r.phone,
        total=int(r.total or 0),
        completed=int(r.completed or 0),
        no_shows=int(r.no_shows or 0),
        total_spent=Decimal(r.spent or 0),
        last_visit=r.last_visit,
    )


async def customers(session: AsyncSession, business_id: str) -> list[CustomerStat]:
    rows = (
        await session.execute(
            _customer_stat_select(business_id).order_by(func.count(Appointment.id).desc())
        )
    ).all()
    return [_row_to_stat(r) for r in rows]


async def customer_detail(
    session: AsyncSession, business_id: str, customer_id: str
) -> CustomerDetail | None:
    row = (
        await session.execute(
            _customer_stat_select(business_id).where(Customer.id == customer_id)
        )
    ).first()
    if row is None:
        return None
    appts = (
        await session.execute(
            _appt_join(business_id)
            .where(Appointment.customer_id == customer_id)
            .order_by(Appointment.start_at.desc())
        )
    ).all()
    base = _row_to_stat(row)
    return CustomerDetail(
        **base.model_dump(),
        appointments=[
            _item(a.Appointment, a.cust_name, a.cust_phone, a.res_name) for a in appts
        ],
    )


async def billing(
    session: AsyncSession, business_id: str, date_from: date, date_to: date
) -> BillingOut:
    start = timez.local(date_from, time.min)
    end = timez.local(date_to + timedelta(days=1), time.min)
    base = [
        Appointment.business_id == business_id,
        Appointment.start_at >= start,
        Appointment.start_at < end,
    ]
    done = [Appointment.status == _St.COMPLETED]

    async def _sum(*conds) -> Decimal:
        total = await session.scalar(
            select(func.coalesce(func.sum(Appointment.price), 0)).where(*conds)
        )
        return Decimal(total or 0)

    billed = await _sum(*base, *done)
    now = timez.now()
    expected = await _sum(
        *base,
        Appointment.status.in_((_St.PENDING, _St.CONFIRMED)),
        Appointment.start_at >= now,
    )
    lost = await _sum(*base, Appointment.status == _St.NO_SHOW)
    appointments = int(
        await session.scalar(select(func.count()).select_from(Appointment).where(*base))
        or 0
    )

    by_status = [
        StatusCount(status=s, count=int(c))
        for s, c in (
            await session.execute(
                select(Appointment.status, func.count())
                .where(*base)
                .group_by(Appointment.status)
            )
        ).all()
    ]

    def _buckets(rows) -> list[BillingBucket]:
        return [
            BillingBucket(key=str(k), revenue=Decimal(rev or 0), count=int(cnt))
            for k, rev, cnt in rows
        ]

    by_service = _buckets(
        (
            await session.execute(
                select(
                    func.coalesce(Appointment.service_name, "—"),
                    func.sum(Appointment.price),
                    func.count(),
                )
                .where(*base, *done)
                .group_by(Appointment.service_name)
            )
        ).all()
    )
    by_professional = _buckets(
        (
            await session.execute(
                select(Resource.name, func.sum(Appointment.price), func.count())
                .join(Resource, Appointment.resource_id == Resource.id)
                .where(*base, *done)
                .group_by(Resource.name)
            )
        ).all()
    )
    by_day = _buckets(
        (
            await session.execute(
                select(
                    func.date(Appointment.start_at),
                    func.sum(Appointment.price),
                    func.count(),
                )
                .where(*base, *done)
                .group_by(func.date(Appointment.start_at))
                .order_by(func.date(Appointment.start_at))
            )
        ).all()
    )

    return BillingOut(
        date_from=date_from,
        date_to=date_to,
        revenue_billed=billed,
        revenue_expected=expected,
        revenue_lost=lost,
        appointments=appointments,
        by_status=by_status,
        by_service=sorted(by_service, key=lambda b: -b.revenue),
        by_professional=sorted(by_professional, key=lambda b: -b.revenue),
        by_day=by_day,
    )
