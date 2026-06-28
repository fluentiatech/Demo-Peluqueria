"""Schemas del login del panel (cambio de API key por cookie de sesión)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class LoginIn(BaseModel):
    api_key: str = Field(..., min_length=1, max_length=256)
    totp: str | None = Field(None, max_length=12)


class LoginOut(BaseModel):
    csrf: str
    ttl_min: int
