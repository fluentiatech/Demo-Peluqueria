"""Schemas del registro de auditoría."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seq: int
    created_at: datetime
    action: str
    method: str
    path: str
    status: int
    actor_ip: str | None = None
    actor_key_fp: str | None = None
    business_id: str | None = None
    hash: str


class AuditVerifyOut(BaseModel):
    ok: bool
    count: int
    broken_seq: int | None = None
