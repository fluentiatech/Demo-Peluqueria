"""Registro de auditoría append-only y a prueba de manipulación (hash-chain).

Cada fila encadena su hash con el de la anterior (`prev_hash`): alterar o borrar
un registro pasado rompe la cadena y `verify_chain` lo detecta. Guarda QUIÉN
(IP + huella de la API key, nunca la clave), QUÉ (método + ruta + estado) y
CUÁNDO, sin PII en el cuerpo. Es una capa de DETECCIÓN independiente del resto.
"""
from __future__ import annotations

from sqlalchemy import BigInteger, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin

GENESIS_HASH = "0" * 64


class AuditLog(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "audit_log"

    # Orden total de la cadena (lo controlamos nosotros, no depende del reloj).
    # `unique` serializa la cadena incluso con escritores concurrentes: una carrera
    # provoca IntegrityError y se reintenta con el seq siguiente.
    seq: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)

    # "mutation" (escritura admin) | "security" (401/403/429) | otros.
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[int] = mapped_column(Integer, nullable=False)

    actor_ip: Mapped[str | None] = mapped_column(String(64))
    # Huella SHA-256 (12 hex) de la API key usada; nunca la clave en claro.
    actor_key_fp: Mapped[str | None] = mapped_column(String(16))
    business_id: Mapped[str | None] = mapped_column(String(36))

    # Marca de tiempo canónica (ISO UTC) incluida en el hash. Es una cadena para
    # que el round-trip por la BD sea EXACTO (los datetime con tz no lo garantizan
    # en SQLite y romperían la verificación de la cadena).
    ts: Mapped[str] = mapped_column(String(32), nullable=False)

    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        Index("ix_audit_action_created", "action", "created_at"),
    )
