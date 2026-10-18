"""Хелперы для синтетических карточек в тестах."""

from __future__ import annotations

from project_doc_agent.validation.cards import compose_card
from project_doc_agent.workspace import Workspace

SECTIONS = [
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
]


def card_content(empty_section: str | None = None, extra_link: str = "") -> str:
    front = {
        "id": "marta-toplog-rc-demo",
        "client": "marta",
        "client_name": "ООО",
        "project": "toplog-rc",
        "project_name": "TopLog",
        "bases": ["ut11-prod"],
        "title": "Демо",
        "type": "integration",
        "profile": "integration",
        "status": "draft",
        "risk": "low",
        "sources": [{"file": "alpha.md", "section": "Раздел", "block_id": "doc-001:b-003"}],
        "review_status": "ok",
        "tags": ["demo"],
        "created": "2026-10-18",
        "updated": "2026-10-18",
        "parent": "marta-toplog-rc",
    }
    lines = ["# Демо", ""]
    for section in SECTIONS:
        lines.extend([f"## {section}", ""])
        if section == empty_section:
            lines.append("")
        elif section == "Источники":
            lines.append("- alpha.md — Раздел (`doc-001:b-003`)")
        elif section == "Связанные заметки":
            lines.append("- [[marta-toplog-rc|Umbrella]]")
            lines.append("- [[index|Индекс]]")
        else:
            lines.append(f"Содержимое раздела «{section}». {extra_link}".strip())
        lines.append("")
    return compose_card(front, "\n".join(lines))


def umbrella_content() -> str:
    front = {
        "id": "marta-toplog-rc",
        "client": "marta",
        "client_name": "ООО",
        "project": "toplog-rc",
        "project_name": "TopLog",
        "bases": ["ut11-prod"],
        "title": "TopLog",
        "type": "project",
        "profile": "integration",
        "status": "draft",
        "risk": "low",
        "sources": [{"file": "alpha.md", "section": "Раздел", "block_id": "doc-001:b-003"}],
        "review_status": "ok",
        "tags": ["demo"],
        "created": "2026-10-18",
        "updated": "2026-10-18",
    }
    body = "\n".join(
        [
            "# TopLog",
            "",
            "## Назначение",
            "Демо.",
            "",
            "## Клиент и базы",
            "ООО, `ut11-prod`.",
            "",
            "## Состав карточек",
            "- [[marta-toplog-rc-demo|Демо]]",
            "",
            "## Источники",
            "- alpha.md (`doc-001:b-003`)",
            "",
            "## Связанные заметки",
            "- [[index|Индекс]]",
            "",
        ]
    )
    return compose_card(front, body)


def write_umbrella(workspace: Workspace) -> None:
    workspace.atomic_write("cards/marta-toplog-rc.md", umbrella_content())
    (workspace.cards_dir / "index.md").write_text("# Индекс\n", encoding="utf-8")
