"""Фабрика провайдера по конфигурации проекта."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from project_doc_agent.config import ProjectConfig, load_providers
from project_doc_agent.providers.base import LLMProvider
from project_doc_agent.providers.cache import ResponseCache
from project_doc_agent.providers.openai_compat import OpenAICompatibleProvider
from project_doc_agent.resources import repository_root


def load_environment() -> None:
    """Загрузить .env из текущего каталога и из корня приложения, не перезаписывая среду."""
    load_dotenv(Path.cwd() / ".env", override=False)
    try:
        load_dotenv(repository_root() / ".env", override=False)
    except Exception:  # noqa: BLE001 - .env не обязателен
        pass


def build_provider(
    config: ProjectConfig,
    *,
    name: str | None = None,
    cache_dir: Path | None = None,
    raw_dir: Path | None = None,
) -> LLMProvider:
    load_environment()
    providers = load_providers()
    provider_config = providers.by_name(name or config.provider)
    cache = ResponseCache(cache_dir) if cache_dir is not None else None
    return OpenAICompatibleProvider(provider_config, cache=cache, raw_dir=raw_dir)
