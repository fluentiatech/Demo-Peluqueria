"""Siembra un negocio de ejemplo con servicios, recursos, horario y reglas.

Muestra también las extensiones del modelo: buffers de limpieza, capacidad
servicio↔profesional, horario propio de un recurso y un día de cierre.

Uso:  python -m scripts.seed
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.database import AsyncSessionLocal, init_db
from app.models import Business, BusinessClosure, Resource, Service
from app.tools import assign_service_resources

# Horario: lunes(0)–viernes(4) 09:00–14:00 y 16:00–20:00; sábado(5) 09:00–14:00.
OPENING_HOURS = {
    "0": [["09:00", "14:00"], ["16:00", "20:00"]],
    "1": [["09:00", "14:00"], ["16:00", "20:00"]],
    "2": [["09:00", "14:00"], ["16:00", "20:00"]],
    "3": [["09:00", "14:00"], ["16:00", "20:00"]],
    "4": [["09:00", "14:00"], ["16:00", "20:00"]],
    "5": [["09:00", "14:00"]],
}

SERVICES = [
    # (nombre, duración_min, precio, categoría, buffer_after_min)
    ("Corte de caballero", 30, "12.00", "corte", 0),
    ("Corte de señora", 45, "18.00", "corte", 0),
    ("Corte + barba", 45, "20.00", "corte", 0),
    ("Tinte", 90, "45.00", "color", 15),
    ("Mechas", 120, "65.00", "color", 15),
    ("Peinado / recogido", 60, "30.00", "peinado", 0),
    ("Manicura", 45, "20.00", "estetica", 0),
]

# Cada recurso (sillón) lleva el nombre del profesional asociado.
RESOURCES = ["María García", "Carlos Ruiz", "Lucía Moreno"]


async def main() -> None:
    await init_db()
    async with AsyncSessionLocal() as session:
        existing = await session.scalar(
            select(Business).where(Business.name == "Peluquería Demo")
        )
        if existing:
            print(f"Ya existe el negocio demo: {existing.id}")
            return

        business = Business(
            name="Peluquería Demo",
            business_type="peluqueria",
            timezone="Europe/Madrid",
            currency="EUR",
            phone="+34600111222",
            address="Calle Mayor 1, Madrid",
            whatsapp_phone_number_id="000000000000000",
            notify_phone="+34600111222",  # avisos de handoff/errores al negocio
            # Personalidad del agente + marca del panel (demo).
            assistant_name="Lucía",
            agent_tone="cercano",
            use_emojis=True,
            agent_language="auto",
            brand_color="#1f6f5c",
            opening_hours=OPENING_HOURS,
            slot_granularity_min=15,
            system_context=(
                "Peluquería de barrio. Tono cercano y profesional. "
                "Política de cancelación: avisar con 24 h de antelación."
            ),
        )
        session.add(business)
        await session.flush()

        services: dict[str, Service] = {}
        for name, dur, price, cat, buf in SERVICES:
            svc = Service(
                business_id=business.id,
                name=name,
                duration_min=dur,
                price=Decimal(price),
                category=cat,
                buffer_after_min=buf,
            )
            session.add(svc)
            services[name] = svc

        resources: dict[str, Resource] = {}
        for rname in RESOURCES:
            res = Resource(business_id=business.id, name=rname)
            session.add(res)
            resources[rname] = res
        await session.flush()

        # Lucía solo trabaja por las mañanas.
        resources["Lucía Moreno"].working_hours = {
            str(d): [["09:00", "14:00"]] for d in range(6)
        }

        # Capacidad: la manicura solo la hace Lucía.
        await assign_service_resources(
            session,
            business.id,
            services["Manicura"].id,
            [resources["Lucía Moreno"].id],
        )

        # Cierre de ejemplo: cerrado dentro de 7 días.
        session.add(
            BusinessClosure(
                business_id=business.id,
                date=date.today() + timedelta(days=7),
                is_closed=True,
                reason="Festivo local",
            )
        )

        await session.commit()
        print(f"Negocio demo creado: {business.id}")
        print(f"  Servicios: {len(SERVICES)} · Recursos: {len(RESOURCES)}")
        print("  Reglas: buffers en color, manicura solo Lucía, "
              "Lucía mañanas, 1 cierre")


if __name__ == "__main__":
    asyncio.run(main())
