"""API del panel de gestión: agenda, clientes y facturación."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import Customer
from app.reporting import service as reporting
from app.schemas.reports import (
    AgendaOut,
    BillingOut,
    CustomerDetail,
    CustomerStat,
    CustomerUpdate,
)

router = APIRouter(prefix="/admin", tags=["panel"])


@router.get("/businesses/{business_id}/agenda", response_model=AgendaOut)
async def agenda(
    business_id: str,
    day: date = Query(..., alias="date"),
    session: AsyncSession = Depends(get_session),
) -> AgendaOut:
    return await reporting.agenda(session, business_id, day)


@router.get("/businesses/{business_id}/customers", response_model=list[CustomerStat])
async def customers(
    business_id: str, session: AsyncSession = Depends(get_session)
) -> list[CustomerStat]:
    return await reporting.customers(session, business_id)


@router.get(
    "/businesses/{business_id}/customers/{customer_id}", response_model=CustomerDetail
)
async def customer_detail(
    business_id: str,
    customer_id: str,
    session: AsyncSession = Depends(get_session),
) -> CustomerDetail:
    detail = await reporting.customer_detail(session, business_id, customer_id)
    if detail is None:
        raise HTTPException(404, "Cliente no encontrado")
    return detail


@router.patch(
    "/businesses/{business_id}/customers/{customer_id}", response_model=CustomerStat
)
async def update_customer(
    business_id: str,
    customer_id: str,
    payload: CustomerUpdate,
    session: AsyncSession = Depends(get_session),
) -> CustomerStat:
    customer = await session.get(Customer, customer_id)
    if customer is None or customer.business_id != business_id:
        raise HTTPException(404, "Cliente no encontrado")
    if payload.name is not None:
        customer.name = payload.name
    await session.flush()
    detail = await reporting.customer_detail(session, business_id, customer_id)
    if detail is None:
        raise HTTPException(404, "Cliente no encontrado")
    return CustomerStat(**{k: getattr(detail, k) for k in CustomerStat.model_fields})


@router.get("/businesses/{business_id}/billing", response_model=BillingOut)
async def billing(
    business_id: str,
    date_from: date = Query(...),
    date_to: date = Query(...),
    session: AsyncSession = Depends(get_session),
) -> BillingOut:
    if date_to < date_from:
        raise HTTPException(400, "date_to no puede ser anterior a date_from")
    return await reporting.billing(session, business_id, date_from, date_to)
