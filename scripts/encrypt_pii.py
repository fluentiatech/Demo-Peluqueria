"""Recifra la PII existente tras activar PII_ENCRYPTION_KEY.

Lee cada fila (el descifrado tolera texto en claro legado) y la vuelve a guardar,
de modo que los tipos de columna cifran el valor al escribir. Idempotente: volver
a ejecutarlo sobre datos ya cifrados los deja igual.

Uso:  PII_ENCRYPTION_KEY=... python -m scripts.encrypt_pii
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app import crypto
from app.database import AsyncSessionLocal
from app.models import Appointment, Conversation, Customer


async def main() -> None:
    if not crypto.is_enabled():
        print("PII_ENCRYPTION_KEY no configurada: nada que cifrar.")
        return

    async with AsyncSessionLocal() as session:
        n = 0
        for customer in (await session.scalars(select(Customer))).all():
            flag_modified(customer, "phone")
            if customer.name is not None:
                flag_modified(customer, "name")
            n += 1
        for convo in (await session.scalars(select(Conversation))).all():
            flag_modified(convo, "customer_phone")
            n += 1
        for appt in (await session.scalars(select(Appointment))).all():
            if appt.notes is not None:
                flag_modified(appt, "notes")
                n += 1
        await session.commit()
        print(f"Filas recifradas: {n}")


if __name__ == "__main__":
    asyncio.run(main())
