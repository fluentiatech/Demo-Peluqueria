"""Purga datos operativos antiguos (retención RGPD + tamaño de tablas).

Uso:  python -m scripts.purge_old
Cron sugerido (a diario, de madrugada):
    30 3 * * * cd /app && python -m scripts.purge_old
"""
from __future__ import annotations

import asyncio

from app.database import AsyncSessionLocal
from app.retention import purge_old


async def main() -> None:
    async with AsyncSessionLocal() as session:
        result = await purge_old(session)
        print(f"Purga completada: {result}")


if __name__ == "__main__":
    asyncio.run(main())
