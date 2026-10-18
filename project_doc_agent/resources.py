"""Пути к ресурсам приложения: prompts, standards, config."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from project_doc_agent.errors import ConfigError


def package_root() -> Path:
    return Path(__file__).resolve().parent


def repository_root() -> Path:
    """Каталог репозитория/установки, где лежат prompts, standards, config."""
    env_root = os.environ.get("DOCAGENT_ROOT")
    if env_root:
        root = Path(env_root).expanduser().resolve()
        if (root / "prompts").is_dir():
            return root
    root = package_root().parent
    if (root / "prompts").is_dir():
        return root
    cwd = Path.cwd()
    if (cwd / "prompts").is_dir():
        return cwd
    raise ConfigError(
        "Не найдены каталоги prompts/standards/config. "
        "Запустите docagent из каталога проекта или задайте DOCAGENT_ROOT."
    )


def prompt_path(name: str) -> Path:
    path = repository_root() / "prompts" / name
    if not path.is_file():
        raise ConfigError(f"Не найден промт: {path}")
    return path


def read_prompt(name: str) -> str:
    return prompt_path(name).read_text(encoding="utf-8")


@lru_cache(maxsize=8)
def standard_sections(profile: str) -> tuple[str, ...]:
    """Обязательные H2-разделы профиля из standards/<profile>.md."""
    path = repository_root() / "standards" / f"{profile}.md"
    if not path.is_file():
        if profile == "integration":
            return (
                "Назначение",
                "Участники и системы",
                "Сценарии обмена",
                "Форматы и методы",
                "Данные и справочники",
                "Ошибки и мониторинг",
                "Как проверить",
                "Ограничения и допущения",
                "Открытые вопросы",
                "Источники",
                "Связанные заметки",
            )
        if profile == "task":
            return ("Зачем", "Что сделано", "Как проверить", "Риски и ограничения", "Связанные заметки")
        raise ConfigError(f"Неизвестный профиль: {profile}")
    sections: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("## ") and not stripped.startswith("### "):
            title = stripped[3:].strip()
            if title:
                sections.append(title)
    if not sections:
        raise ConfigError(f"В стандарте {path} не найдены H2-разделы")
    return tuple(sections)


def providers_path() -> Path:
    env_path = os.environ.get("DOCAGENT_PROVIDERS")
    candidates: list[Path] = []
    if env_path:
        candidates.append(Path(env_path).expanduser())
    try:
        candidates.append(repository_root() / "config" / "providers.yaml")
    except ConfigError:
        pass
    candidates.append(Path.cwd() / "config" / "providers.yaml")
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise ConfigError(
        "Не найден config/providers.yaml. Задайте DOCAGENT_PROVIDERS или запустите из каталога проекта."
    )
