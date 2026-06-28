"""Ofrece los huecos liberados (cancelaciones) a la lista de espera.

Uso:  python -m scripts.fill_waitlist
Cron sugerido (cada minuto, para que el aviso sea casi inmediato):
    * * * * * cd /app && python -m scripts.fill_waitlist
"""
from __future__ import annotations

import asyncio

from app.database import AsyncSessionLocal
from app.waitlist import process_freed_slots


async def main() -> None:
    async with AsyncSessionLocal() as session:
        n = await process_freed_slots(session)
        print(f"Huecos ofrecidos a la lista de espera: {n}")


if __name__ == "__main__":
    asyncio.run(main())
