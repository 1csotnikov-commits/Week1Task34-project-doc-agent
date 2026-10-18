"""Базовые протоколы провайдеров LLM."""

from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

TModel = TypeVar("TModel", bound=BaseModel)


class LLMResponse(BaseModel):
    content: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_ms: int = 0
    cached: bool = False
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMProvider(Protocol):
    name: str
    model: str

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: type[TModel],
        purpose: str,
        cache_scope: str = "",
    ) -> tuple[TModel, LLMResponse]: ...
