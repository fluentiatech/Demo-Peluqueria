"""Auditoría a prueba de manipulación y detección de picos de seguridad.

- `record`: inserta una entrada encadenada por hash (append-only).
- `verify_chain`: recomputa la cadena y detecta cualquier alteración/borrado.
- `scan_security`: si los eventos de seguridad (401/403/429) superan un umbral en
  la ventana, emite un `EventLog` de tipo "error" que el cron de avisos
  (`notifications.send_pending_alerts`) entrega al negocio. Así reutiliza toda la
  maquinaria de entrega + idempotencia existente.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AuditLog, EventLog
from app.models.audit_log import GENESIS_HASH

logger = logging.getLogger("agente-citas.audit")

_BIZ_RE = re.compile(r"/businesses/([0-9a-fA-F-]{36})")


def key_fingerprint(api_key: str | None) -> str | None:
    """Huella corta (12 hex) de la API key: identifica la clave sin almacenarla."""
    if not api_key:
        return None
    return hashlib.sha256(api_key.encode()).hexdigest()[:12]


def business_from_path(path: str) -> str | None:
    m = _BIZ_RE.search(path)
    return m.group(1) if m else None


def _row_hash(
    *,
    seq: int,
    prev_hash: str,
    ts: str,
    action: str,
    method: str,
    path: str,
    status: int,
    actor_ip: str | None,
    actor_key_fp: str | None,
    business_id: str | None,
) -> str:
    canonical = "|".join(
        [
            str(seq),
            prev_hash,
            ts,
            action,
            method,
            path,
            str(status),
            actor_ip or "",
            actor_key_fp or "",
            business_id or "",
        ]
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def record(
    session: AsyncSession,
    *,
    action: str,
    method: str,
    path: str,
    status: int,
    actor_ip: str | None = None,
    actor_key_fp: str | None = None,
    business_id: str | None = None,
) -> AuditLog:
    """Añade una entrada encadenada. Reintenta una vez si hay carrera en `seq`."""
    for _attempt in range(2):
        last = (
            await session.execute(
                select(AuditLog.seq, AuditLog.hash).order_by(AuditLog.seq.desc()).limit(1)
            )
        ).first()
        seq = (last.seq + 1) if last else 1
        prev_hash = last.hash if last else GENESIS_HASH
        now = datetime.now(UTC)
        ts = now.isoformat()
        digest = _row_hash(
            seq=seq, prev_hash=prev_hash, ts=ts, action=action,
            method=method, path=path, status=status, actor_ip=actor_ip,
            actor_key_fp=actor_key_fp, business_id=business_id,
        )
        entry = AuditLog(
            seq=seq, action=action, method=method, path=path, status=status,
            actor_ip=actor_ip, actor_key_fp=actor_key_fp, business_id=business_id,
            prev_hash=prev_hash, hash=digest, created_at=now, ts=ts,
        )
        session.add(entry)
        try:
            async with session.begin_nested():
                await session.flush()
            return entry
        except IntegrityError:
            await session.rollback()  # otra escritura ganó el seq; reintenta
    raise RuntimeError("No se pudo encadenar la entrada de auditoría")


async def verify_chain(session: AsyncSession) -> dict:
    """Recomputa la cadena. Devuelve {ok, count, broken_seq?}."""
    rows = (await session.scalars(select(AuditLog).order_by(AuditLog.seq))).all()
    prev = GENESIS_HASH
    expected_seq = 1
    for r in rows:
        digest = _row_hash(
            seq=r.seq, prev_hash=r.prev_hash, ts=r.ts, action=r.action,
            method=r.method, path=r.path, status=r.status, actor_ip=r.actor_ip,
            actor_key_fp=r.actor_key_fp, business_id=r.business_id,
        )
        if r.seq != expected_seq or r.prev_hash != prev or r.hash != digest:
            return {"ok": False, "count": len(rows), "broken_seq": r.seq}
        prev = r.hash
        expected_seq += 1
    return {"ok": True, "count": len(rows), "broken_seq": None}


async def scan_security(
    session: AsyncSession, *, now: datetime | None = None
) -> int:
    """Emite un EventLog 'error' por cada negocio si hay un pico de eventos de
    seguridad en la ventana. Devuelve el nº de alertas emitidas."""
    if settings.security_alert_threshold <= 0:
        return 0
    now = now or datetime.now(UTC)
    since = now - timedelta(minutes=settings.security_alert_window_min)

    rows = (
        await session.execute(
            select(AuditLog.business_id, func.count())
            .where(AuditLog.action == "security", AuditLog.created_at >= since)
            .group_by(AuditLog.business_id)
        )
    ).all()

    emitted = 0
    for business_id, count in rows:
        if count < settings.security_alert_threshold:
            continue
        # Evita duplicar la alerta si ya hay una sin notificar en esta ventana.
        existing = await session.scalar(
            select(func.count())
            .select_from(EventLog)
            .where(
                EventLog.type == "error",
                EventLog.notified_at.is_(None),
                EventLog.created_at >= since,
                EventLog.business_id == business_id,
            )
        )
        if existing:
            continue
        session.add(
            EventLog(
                business_id=business_id,
                type="error",
                payload={
                    "kind": "security_spike",
                    "count": int(count),
                    "window_min": settings.security_alert_window_min,
                },
            )
        )
        emitted += 1
    if emitted:
        await session.commit()
    return emitted
