"""Cliente LLM sobre OpenAI/ChatGPT.

Se expone como un `Protocol` para que la lógica de negocio dependa de una
interfaz, no del SDK concreto: así los tests inyectan un cliente falso y, si en
el futuro se cambia de proveedor, solo se toca este módulo.

Sobre coste: el bloque de contexto del negocio va siempre como mensaje `system`
(prefijo estable). OpenAI cachea automáticamente los prefijos largos (≥1024
tokens), por lo que las lecturas repetidas cuestan una fracción. Registramos los
tokens (incluidos los cacheados) para la observabilidad de coste.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.config import settings

# Un turno de conversación tal como lo espera la API de chat.
Message = dict[str, str]


class LLMNotConfiguredError(RuntimeError):
    """No hay credenciales del proveedor LLM configuradas."""


@dataclass
class LLMResult:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0


@dataclass
class Extraction:
    """Resultado de una extracción estructurada (NLU): datos + coste."""

    data: dict[str, Any] = field(default_factory=dict)
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0


@runtime_checkable
class LLMClient(Protocol):
    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 600,
    ) -> LLMResult: ...

    async def extract(
        self, *, system: str, user: str, model: str | None = None
    ) -> Extraction: ...


class OpenAIClient:
    """Implementación real contra la API de OpenAI."""

    def __init__(
        self, api_key: str | None = None, default_model: str | None = None
    ) -> None:
        from openai import AsyncOpenAI

        key = api_key or settings.openai_api_key
        if not key:
            raise LLMNotConfiguredError(
                "OPENAI_API_KEY no configurada: el agente no puede llamar al modelo"
            )
        self._client = AsyncOpenAI(api_key=key, timeout=settings.openai_timeout_s)
        self._default_model = default_model or settings.openai_model_fast

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 600,
    ) -> LLMResult:
        used_model = model or self._default_model
        full_messages: list[Any] = [{"role": "system", "content": system}, *messages]
        resp = await self._client.chat.completions.create(
            model=used_model,
            messages=full_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        usage = resp.usage
        cached = 0
        if usage is not None and usage.prompt_tokens_details is not None:
            cached = usage.prompt_tokens_details.cached_tokens or 0
        return LLMResult(
            text=(resp.choices[0].message.content or "").strip(),
            model=used_model,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            cached_tokens=cached,
        )

    async def extract(
        self, *, system: str, user: str, model: str | None = None
    ) -> Extraction:
        used_model = model or self._default_model
        resp = await self._client.chat.completions.create(
            model=used_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0,
            max_tokens=300,
            response_format={"type": "json_object"},
        )
        try:
            data = json.loads(resp.choices[0].message.content or "{}")
        except json.JSONDecodeError:
            data = {}
        usage = resp.usage
        cached = 0
        if usage is not None and usage.prompt_tokens_details is not None:
            cached = usage.prompt_tokens_details.cached_tokens or 0
        return Extraction(
            data=data if isinstance(data, dict) else {},
            model=used_model,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            cached_tokens=cached,
        )


def get_llm_client() -> LLMClient:
    """Dependencia FastAPI: devuelve el cliente LLM configurado.

    Los tests sobrescriben esta dependencia con un cliente falso.
    """
    return OpenAIClient()
