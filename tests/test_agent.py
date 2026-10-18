"""Тесты агентного режима и tool-слоя."""

from __future__ import annotations

import pytest

from project_doc_agent.agent.runner import run_agent
from project_doc_agent.config import ProjectConfig
from project_doc_agent.errors import PlanError
from project_doc_agent.models import RunStatus
from project_doc_agent.runs.manifest import RunContext
from project_doc_agent.tools.registry import ToolLayer
from project_doc_agent.workspace import Workspace
from tests.fakes import FakeProvider
from tests.helpers import card_content, write_umbrella


def _tool_layer(ws: Workspace, config: ProjectConfig, run: RunContext) -> ToolLayer:
    return ToolLayer(
        workspace=ws,
        config=config,
        source_dir=config.source_path(ws.root),
        provider=FakeProvider(),
        run=run,
    )


def _plan() -> list[dict]:
    return [
        {
            "tool": "write_card",
            "args": {"filename": "marta-toplog-rc-demo.md", "content": card_content()},
        }
    ]


def test_tool_layer_blocks_path_traversal(workspace: tuple[Workspace, ProjectConfig]) -> None:
    ws, config = workspace
    run = RunContext.create(ws, "test-tools")
    tools = _tool_layer(ws, config, run)
    result = tools.execute("write_file", {"path": "../outside.md", "content": "hack"})
    assert not result.ok
    assert not (ws.root.parent / "outside.md").exists()
    read = tools.execute("read_file", {"path": "../outside.md"})
    assert not read.ok


def test_agent_writes_card_after_validation(workspace: tuple[Workspace, ProjectConfig]) -> None:
    ws, config = workspace
    write_umbrella(ws)
    run = RunContext.create(ws, "test-run", goal="Собери карточку")
    outcome = run_agent(
        workspace=ws,
        config=config,
        provider=FakeProvider(plan_steps=_plan()),
        source_dir=config.source_path(ws.root),
        goal="Собери карточку",
        run=run,
    )
    assert outcome.completed
    assert "cards/marta-toplog-rc-demo.md" in outcome.written_files
    assert (ws.cards_dir / "marta-toplog-rc-demo.md").is_file()
    assert run.manifest.status is RunStatus.completed


def test_agent_dry_run_does_not_write(workspace: tuple[Workspace, ProjectConfig]) -> None:
    ws, config = workspace
    write_umbrella(ws)
    run = RunContext.create(ws, "test-run-dry", goal="Собери карточку")
    outcome = run_agent(
        workspace=ws,
        config=config,
        provider=FakeProvider(plan_steps=_plan()),
        source_dir=config.source_path(ws.root),
        goal="Собери карточку",
        run=run,
        dry_run=True,
    )
    assert outcome.completed
    assert not (ws.cards_dir / "marta-toplog-rc-demo.md").exists()
    assert (run.run_dir / "staged" / "cards" / "marta-toplog-rc-demo.md").is_file()


def test_write_card_fills_metadata_and_creates_umbrella(workspace: tuple[Workspace, ProjectConfig]) -> None:
    ws, config = workspace
    run = RunContext.create(ws, "test-autofill")
    tools = _tool_layer(ws, config, run)
    content = """---
title: "Интеграция TopLog WMS и РЦ"
project: "Интеграция TopLog WMS и РЦ"
---

# Интеграция

Краткое описание без обязательных разделов.
"""
    result = tools.execute(
        "write_card",
        {"filename": "integration.md", "content": content},
    )
    assert result.ok, result.error
    assert "cards/integration.md" in tools.staged
    assert "cards/marta-toplog-rc.md" in tools.staged
    assert "cards/index.md" in tools.staged
    card_text = tools.staged["cards/integration.md"]
    assert "project: toplog-rc" in card_text
    assert "project_name:" in card_text
    assert "sources:" in card_text
    assert "## Назначение" in card_text
    assert "review_status: needs_review" in card_text
    validation = tools.validate()
    assert validation.ok, validation.output


def test_write_card_accepts_plain_markdown(workspace: tuple[Workspace, ProjectConfig]) -> None:
    ws, config = workspace
    run = RunContext.create(ws, "test-plain-card")
    tools = _tool_layer(ws, config, run)
    result = tools.execute(
        "write_card",
        {"filename": "plain.md", "content": "# Простая карточка\n\nТекст без front matter."},
    )
    assert result.ok, result.error
    card_text = tools.staged["cards/plain.md"]
    assert card_text.startswith("---\n")
    assert "id: marta-toplog-rc-prostaya-kartochka" in card_text
    assert "## Источники" in card_text

def test_build_cards_creates_full_set(workspace: tuple[Workspace, ProjectConfig]) -> None:
    ws, config = workspace
    run = RunContext.create(ws, "test-build-cards")
    tools = _tool_layer(ws, config, run)
    extraction = tools.execute("extract", {})
    assert extraction.ok, extraction.error
    build = tools.execute("build_cards", {})
    assert build.ok, build.error
    assert int(build.data.get("cards", 0)) >= 3
    for name in (
        "cards/marta-toplog-rc.md",
        "cards/index.md",
        "reports/sources.md",
        "reports/open_questions.md",
        "reports/conflicts.md",
        "reports/manual_review.md",
    ):
        assert name in tools.staged, name
    validation = tools.validate()
    assert validation.ok, validation.output


def test_agent_unknown_tool_fails_without_writes(workspace: tuple[Workspace, ProjectConfig]) -> None:

    ws, config = workspace
    run = RunContext.create(ws, "test-run-bad", goal="Сломай план")
    with pytest.raises(PlanError):
        run_agent(
            workspace=ws,
            config=config,
            provider=FakeProvider(plan_steps=[{"tool": "delete_everything", "args": {}}]),
            source_dir=config.source_path(ws.root),
            goal="Сломай план",
            run=run,
        )
    assert run.manifest.status is RunStatus.failed
    assert not list(ws.cards_dir.glob("*.md"))


def test_agent_resume_uses_state(workspace: tuple[Workspace, ProjectConfig]) -> None:
    ws, config = workspace
    run = RunContext.create(ws, "test-resume", goal="Продолжи")
    run.write_json(
        "state.json",
        {
            "goal": "Продолжи",
            "steps_done": 1,
            "observations": [{"step": 1, "tool": "outline", "ok": True, "output": "ok"}],
            "staged": {},
            "plan_rounds": 1,
        },
    )
    outcome = run_agent(
        workspace=ws,
        config=config,
        provider=FakeProvider(plan_steps=[]),
        source_dir=config.source_path(ws.root),
        goal="Продолжи",
        run=run,
        resume=True,
    )
    assert outcome.completed
    assert run.manifest.status is RunStatus.completed
