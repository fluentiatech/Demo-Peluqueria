"""Envía los recordatorios de cita pendientes.

Uso:  python -m scripts.send_reminders
Pensado para un cron, p. ej. cada 15 minutos:
    */15 * * * * cd /app && python -m scripts.send_reminders
"""
from __future__ import annotations

import asyncio

from app.database import AsyncSessionLocal
from app.reminders import send_due_reminders


async def main() -> None:
    async with AsyncSessionLocal() as session:
        sent = await send_due_reminders(session)
        print(f"Recordatorios enviados: {sent}")


if __name__ == "__main__":
    asyncio.run(main())
