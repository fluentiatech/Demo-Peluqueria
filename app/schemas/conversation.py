"""Schemas de conversación (bandeja de handoff)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.conversation import ConversationState


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    customer_phone: str
    state: ConversationState
    updated_at: datetime
    context: dict[str, Any]  # incluye la ventana reciente de la conversación
