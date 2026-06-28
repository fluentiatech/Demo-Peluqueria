"""Avisa al negocio de handoffs pendientes y errores del agente.

Uso:  python -m scripts.send_alerts
Cron sugerido (cada 5 minutos):
    */5 * * * * cd /app && python -m scripts.send_alerts
"""
from __future__ import annotations

import asyncio

from app.audit import scan_security
from app.database import AsyncSessionLocal
from app.notifications import send_pending_alerts


async def main() -> None:
    async with AsyncSessionLocal() as session:
        # Primero detecta picos de seguridad (emite EventLog), luego entrega todo.
        spikes = await scan_security(session)
        n = await send_pending_alerts(session)
        print(f"Alertas de seguridad emitidas: {spikes} · Eventos notificados: {n}")


if __name__ == "__main__":
    asyncio.run(main())
