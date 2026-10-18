"""OpenAI-совместимый провайдер (DeepSeek, OpenRouter, Mistral)."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, TypeVar

from openai import OpenAI
from pydantic import BaseModel

from project_doc_agent.config import ProviderConfig
from project_doc_agent.errors import ProviderError, ProviderResponseError
from project_doc_agent.providers.base import LLMResponse
from project_doc_agent.providers.cache import ResponseCache
from project_doc_agent.utils import extract_json_object, truncate

TModel = TypeVar("TModel", bound=BaseModel)


def sanitize_error(text: str) -> str:
    """Убрать из текста ошибки потенциальные ключи."""
    text = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-***", text)
    text = re.sub(r"(?i)(authorization:\s*bearer\s+)\S+", r"\1***", text)
    return text


class OpenAICompatibleProvider:
    """Единый клиент для облачных OpenAI-совместимых API."""

    def __init__(
        self,
        config: ProviderConfig,
        *,
        cache: ResponseCache | None = None,
        client: Any | None = None,
        api_key: str | None = None,
        raw_dir: Path | None = None,
    ) -> None:
        self.config = config
        self.name = config.name
        self.model = config.model
        self.cache = cache
        self.raw_dir = raw_dir
        self._raw_counter = 0
        key = api_key or os.environ.get(config.api_key_env, "").strip()
        if not key:
            raise ProviderError(
                f"Не задан API-ключ провайдера {config.name!r}. "
                f"Установите переменную окружения {config.api_key_env} в .env или в среде."
            )
        self._client: Any = client or OpenAI(
            api_key=key,
            base_url=config.base_url,
            timeout=180.0,
            max_retries=0,
        )

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: type[TModel],
        purpose: str,
        cache_scope: str = "",
    ) -> tuple[TModel, LLMResponse]:
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False, sort_keys=True)
        system_with_schema = f"{system}\n\nJSON Schema ответа:\n{schema_json}"
        cache_payload = {
            "provider": self.name,
            "base_url": self.config.base_url,
            "model": self.model,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "system": system_with_schema,
            "user": user,
            "schema": schema.__name__,
            "scope": cache_scope,
        }
        cache_key = self.cache.key(cache_payload) if self.cache else ""
        if self.cache and cache_key:
            cached = self.cache.get(cache_key)
            if cached is not None:
                parsed = self._parse_and_validate(cached["content"], schema)
                response = LLMResponse(
                    content=cached["content"],
                    provider=self.name,
                    model=str(cached.get("model") or self.model),
                    duration_ms=0,
                    cached=True,
                )
                return parsed, response

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_with_schema},
            {"role": "user", "content": user},
        ]
        content, usage, duration_ms, used_model = self._request(messages)
        repaired = False
        try:
            parsed = self._parse_and_validate(content, schema)
        except ProviderResponseError as first_error:
            content, usage, repair_duration, used_model = self._repair(
                messages, content, first_error, usage, used_model
            )
            duration_ms += repair_duration
            repaired = True
            parsed = self._parse_and_validate(content, schema)

        prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        cost = (
            prompt_tokens / 1_000_000 * self.config.input_cost_per_1m
            + completion_tokens / 1_000_000 * self.config.output_cost_per_1m
        )
        response = LLMResponse(
            content=content,
            provider=self.name,
            model=used_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_ms=duration_ms,
            cost_usd=round(cost, 6),
        )
        if self.cache and cache_key:
            self.cache.put(
                cache_key,
                {
                    "content": content,
                    "model": used_model,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "purpose": purpose,
                    "repaired": repaired,
                },
            )
        self._save_raw(purpose, content)
        return parsed, response

    def _request(self, messages: list[dict[str, str]]) -> tuple[str, dict[str, int], int, str]:
        started = time.perf_counter()
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                response_format={"type": "json_object"},
            )
        except Exception as exc:  # noqa: BLE001 - внешний SDK
            message = truncate(sanitize_error(str(exc)), 500)
            raise ProviderError(f"Ошибка вызова {self.name} ({self.config.base_url}): {message}") from exc
        duration_ms = int((time.perf_counter() - started) * 1000)
        try:
            content = response.choices[0].message.content or ""
        except (AttributeError, IndexError, TypeError) as exc:
            raise ProviderResponseError(f"{self.name}: пустой или неожиданный ответ API") from exc
        if not content.strip():
            raise ProviderResponseError(f"{self.name}: модель вернула пустой ответ")
        usage_obj = getattr(response, "usage", None)
        usage = {
            "prompt_tokens": int(getattr(usage_obj, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(usage_obj, "completion_tokens", 0) or 0),
        }
        used_model = str(getattr(response, "model", self.model) or self.model)
        return content, usage, duration_ms, used_model

    def _repair(
        self,
        messages: list[dict[str, str]],
        bad_content: str,
        error: ProviderResponseError,
        usage: dict[str, int],
        used_model: str,
    ) -> tuple[str, dict[str, int], int, str]:
        repair_messages = [
            *messages,
            {"role": "assistant", "content": bad_content},
            {
                "role": "user",
                "content": (
                    "Предыдущий ответ не прошёл JSON-валидацию. "
                    f"Ошибка: {truncate(str(error), 800)}. "
                    "Верни исправленный ответ строго как один JSON-объект по схеме. "
                    "Не добавляй markdown, пояснения и комментарии."
                ),
            },
        ]
        content, repair_usage, duration_ms, repair_model = self._request(repair_messages)
        combined = {
            "prompt_tokens": int(usage.get("prompt_tokens", 0)) + int(repair_usage.get("prompt_tokens", 0)),
            "completion_tokens": int(usage.get("completion_tokens", 0))
            + int(repair_usage.get("completion_tokens", 0)),
        }
        return content, combined, duration_ms, repair_model or used_model

    @staticmethod
    def _parse_and_validate(content: str, schema: type[TModel]) -> TModel:
        candidate = extract_json_object(content)
        try:
            raw = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise ProviderResponseError(f"Ответ модели не является JSON: {exc}") from exc
        try:
            return schema.model_validate(raw)
        except ValueError as exc:
            raise ProviderResponseError(
                f"Ответ модели не соответствует схеме {schema.__name__}: {exc}"
            ) from exc

    def _save_raw(self, purpose: str, content: str) -> None:
        if self.raw_dir is None:
            return
        self._raw_counter += 1
        safe_purpose = "".join(char if char.isalnum() or char in "-_" else "-" for char in purpose)[:60]
        path = self.raw_dir / f"{self._raw_counter:03d}-{safe_purpose}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
