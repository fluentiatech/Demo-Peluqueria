"""Schemas del agente conversacional."""
from __future__ import annotations

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)


class AskResponse(BaseModel):
    answer: str
