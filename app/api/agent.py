"""API del agente: endpoint de Q&A para probar el negocio sin WhatsApp."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.llm import LLMClient, get_llm_client
from app.agent.qa import answer_question
from app.database import get_session
from app.models import Business
from app.schemas.agent import AskRequest, AskResponse
from app.security import rate_limit

router = APIRouter(prefix="/admin", tags=["agent"])


@router.post(
    "/businesses/{business_id}/ask",
    response_model=AskResponse,
    dependencies=[Depends(rate_limit("ask"))],
)
async def ask(
    business_id: str,
    payload: AskRequest,
    session: AsyncSession = Depends(get_session),
    llm: LLMClient = Depends(get_llm_client),
) -> AskResponse:
    business = await session.get(Business, business_id)
    if business is None:
        raise HTTPException(404, "Negocio no encontrado")
    answer = await answer_question(session, business, payload.message, llm)
    return AskResponse(answer=answer)
