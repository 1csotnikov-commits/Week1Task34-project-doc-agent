"""Доменные ошибки приложения."""

from __future__ import annotations


class DocAgentError(Exception):
    """Базовая ошибка приложения с сообщением для пользователя."""


class ConfigError(DocAgentError):
    """Ошибка конфигурации проекта или провайдера."""


class WorkspaceError(DocAgentError):
    """Ошибка workspace, путей или прав записи."""


class IngestError(DocAgentError):
    """Ошибка пайплайна ingest."""


class ProviderError(DocAgentError):
    """Ошибка провайдера LLM (ключ, сеть, ответ)."""


class ProviderResponseError(ProviderError):
    """Ответ LLM не прошёл валидацию."""


class CardValidationError(DocAgentError):
    """Ошибка проверки карточек."""


class ToolError(DocAgentError):
    """Ошибка инструмента агентного режима."""


class PlanError(DocAgentError):
    """Невалидный план агента."""


class DiffApplyError(DocAgentError):
    """Не удалось применить unified diff."""
