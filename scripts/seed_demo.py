"""Datos de demostración para el panel: clientes y citas con estados variados.

Inserta las citas directamente (sin pasar por la validación de horario) para que
la agenda, los clientes y la facturación se vean poblados en cualquier fecha.

Uso:  python -m scripts.seed_demo
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select

from app.database import AsyncSessionLocal
from app.models import (
    Appointment,
    AppointmentStatus,
    Business,
    Customer,
    Resource,
    Service,
)

_St = AppointmentStatus

CLIENTES = [
    ("Lucía Bernal", "+34611204876"),
    ("Mateo Quintana", "+34622918340"),
    ("Irene Castaño", "+34633471092"),
    ("Aitor Rey", "+34644650218"),
    ("Noa Vidal", "+34655329471"),
    ("Bruno Salgado", "+34666017593"),
    ("Carmen Olivares", "+34677842106"),
]


async def main() -> None:
    async with AsyncSessionLocal() as session:
        business = await session.scalar(
            select(Business).where(Business.name == "Peluquería Demo")
        )
        if business is None:
            print("Primero ejecuta: python -m scripts.seed")
            return
        existing = await session.scalar(
            select(func.count())
            .select_from(Appointment)
            .where(Appointment.business_id == business.id)
        )
        if existing:
            print(f"Ya hay {existing} citas; no añado demo.")
            return

        services = {
            s.name: s
            for s in (
                await session.scalars(
                    select(Service).where(Service.business_id == business.id)
                )
            ).all()
        }
        resources = (
            await session.scalars(
                select(Resource)
                .where(Resource.business_id == business.id)
                .order_by(Resource.name)
            )
        ).all()

        customers = []
        for name, phone in CLIENTES:
            c = Customer(business_id=business.id, phone=phone, name=name)
            session.add(c)
            customers.append(c)
        await session.flush()

        def book(day: date, hh: int, mm: int, svc: str, res: Resource, cust, status):
            s = services[svc]
            start = datetime.combine(day, time(hh, mm))
            end = start + timedelta(minutes=s.duration_min)
            session.add(
                Appointment(
                    business_id=business.id,
                    service_id=s.id,
                    resource_id=res.id,
                    customer_id=cust.id,
                    start_at=start,
                    end_at=end,
                    block_start_at=start,
                    block_end_at=end,
                    status=status,
                    service_name=s.name,
                    price=s.price,
                    duration_min=s.duration_min,
                )
            )

        today = date.today()
        r0, r1, r2 = resources[0], resources[1], resources[2]

        # Hoy: mezcla de estados.
        book(today, 10, 0, "Corte de caballero", r0, customers[0], _St.COMPLETED)
        book(today, 11, 0, "Tinte", r1, customers[1], _St.CONFIRMED)
        book(today, 12, 0, "Corte + barba", r0, customers[2], _St.PENDING)
        book(today, 10, 30, "Manicura", r2, customers[3], _St.NO_SHOW)
        book(today, 16, 30, "Mechas", r1, customers[4], _St.CONFIRMED)
        book(today, 13, 0, "Corte de señora", r0, customers[5], _St.COMPLETED)

        # Días anteriores (histórico de facturación).
        d2, d4, d6 = (today - timedelta(days=n) for n in (2, 4, 6))
        book(d2, 10, 0, "Tinte", r1, customers[0], _St.COMPLETED)
        book(d2, 11, 0, "Corte de caballero", r0, customers[6], _St.COMPLETED)
        book(d4, 12, 0, "Mechas", r1, customers[2], _St.COMPLETED)
        book(d4, 9, 30, "Peinado / recogido", r0, customers[4], _St.NO_SHOW)
        book(d6, 17, 0, "Corte de señora", r0, customers[5], _St.COMPLETED)

        await session.commit()
        print(f"Demo lista: {len(customers)} clientes y 11 citas.")


if __name__ == "__main__":
    asyncio.run(main())
