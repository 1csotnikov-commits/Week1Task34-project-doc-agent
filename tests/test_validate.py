"""Тесты валидации, diff и --apply."""

from __future__ import annotations

from project_doc_agent.config import ProjectConfig
from project_doc_agent.validation.cards import compose_card
from project_doc_agent.validation.diff import apply_unified_diff, make_diff
from project_doc_agent.validation.validator import validate_workspace
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


def _body(*, empty_section: str | None = None, extra_link: str = "") -> str:
    lines = ["# Демо-карточка", ""]
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
    return "\n".join(lines)


def _front_matter(**overrides):
    data = {
        "id": "marta-toplog-rc-demo",
        "client": "marta",
        "client_name": "ООО",
        "project": "toplog-rc",
        "project_name": "TopLog",
        "bases": ["ut11-prod"],
        "title": "Демо-карточка",
        "type": "integration",
        "profile": "integration",
        "status": "draft",
        "risk": "medium",
        "sources": [{"file": "alpha.md", "section": "Раздел", "block_id": "doc-001:b-003"}],
        "review_status": "needs_review",
        "tags": ["demo"],
        "created": "2026-10-18",
        "updated": "2026-10-18",
        "parent": "marta-toplog-rc",
    }
    data.update(overrides)
    return data


def _write_umbrella(ws: Workspace) -> None:
    data = {
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
        "risk": "medium",
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
            "Клиент: ООО, база `ut11-prod`.",
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
    ws.atomic_write("cards/marta-toplog-rc.md", compose_card(data, body))
    (ws.cards_dir / "index.md").write_text("# Индекс\n", encoding="utf-8")


def test_validate_diff_apply_and_backup(workspace: tuple[Workspace, ProjectConfig]) -> None:
    ws, config = workspace
    _write_umbrella(ws)
    ws.atomic_write(
        "cards/marta-toplog-rc-demo.md",
        compose_card(_front_matter(), _body(empty_section="Ограничения и допущения")),
    )
    result = validate_workspace(ws, config)
    assert result.errors
    assert any(issue.code == "review_note" for issue in result.errors)
    assert any(issue.code == "empty_section" for issue in result.errors)
    assert result.diff
    assert "review_note" in result.diff
    assert (ws.reports_dir / "validation.md").is_file()
    assert not result.applied

    applied = validate_workspace(ws, config, apply=True, run=None)
    assert applied.applied, applied.apply_error
    text = (ws.cards_dir / "marta-toplog-rc-demo.md").read_text(encoding="utf-8")
    assert "review_note:" in text
    assert "Нет данных в источниках." in text
    backups = list((ws.docagent_dir / "runs").rglob("cards/marta-toplog-rc-demo.md"))
    assert backups


def test_apply_refused_for_broken_wiki_link(workspace: tuple[Workspace, ProjectConfig]) -> None:
    ws, config = workspace
    _write_umbrella(ws)
    ws.atomic_write(
        "cards/marta-toplog-rc-demo.md",
        compose_card(_front_matter(), _body(empty_section="Ограничения и допущения", extra_link="[[missing-card]]")),
    )
    before = (ws.cards_dir / "marta-toplog-rc-demo.md").read_text(encoding="utf-8")
    result = validate_workspace(ws, config, apply=True)
    assert not result.applied
    assert result.apply_error
    assert any(issue.code == "wiki_link" for issue in result.errors)
    after = (ws.cards_dir / "marta-toplog-rc-demo.md").read_text(encoding="utf-8")
    assert before == after


def test_diff_apply_roundtrip() -> None:
    old = "line1\nline2\nline3\n"
    new = "line1\nline2 changed\nline3\nline4\n"
    diff = make_diff("cards/a.md", old, new)
    assert apply_unified_diff(old, diff) == new
