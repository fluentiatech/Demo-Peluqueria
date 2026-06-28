"""Servicio de Q&A: responde dudas del negocio en lenguaje natural.

El LLM solo lee el contexto cacheado y redacta. Registra cada llamada en
`events_log` para la observabilidad de coste por conversación. El modelo se elige
por complejidad (routing): la mayoría de dudas van al modelo rápido.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import get_business_context
from app.agent.llm import LLMClient, Message
from app.agent.routing import choose_model
from app.models import Business, EventLog


async def answer_question(
    session: AsyncSession,
    business: Business,
    user_text: str,
    llm: LLMClient,
    history: list[Message] | None = None,
    model: str | None = None,
) -> str:
    """Genera la respuesta del agente a un mensaje del cliente."""
    system = (await get_business_context(session, business)).system_prompt
    messages: list[Message] = [*(history or []), {"role": "user", "content": user_text}]

    result = await llm.complete(
        system=system,
        messages=messages,
        model=model or choose_model(user_text),
    )

    session.add(
        EventLog(
            business_id=business.id,
            type="llm_call",
            payload={
                "kind": "qa",
                "model": result.model,
                "prompt_tokens": result.prompt_tokens,
                "cached_tokens": result.cached_tokens,
                "completion_tokens": result.completion_tokens,
            },
        )
    )
    await session.flush()
    return result.text
