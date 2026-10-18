"""Тесты ingest-пайплайна и воспроизводимости."""

from __future__ import annotations

from project_doc_agent.config import ProjectConfig
from project_doc_agent.ingest.pipeline import run_ingest
from project_doc_agent.models import RunStatus
from project_doc_agent.runs.manifest import RunContext
from project_doc_agent.workspace import Workspace
from tests.fakes import FakeProvider


def _run(ws: Workspace, config: ProjectConfig, *, dry_run: bool = False):
    source = config.source_path(ws.root)
    run = RunContext.create(
        ws,
        "test-ingest",
        provider="deepseek",
        model="fake-model",
        prompt_names=("extract.md",),
    )
    outcome = run_ingest(
        workspace=ws,
        config=config,
        provider=FakeProvider(),
        source_dir=source,
        run=run,
        dry_run=dry_run,
    )
    return outcome, run


def test_ingest_creates_cards_and_reports(workspace: tuple[Workspace, ProjectConfig]) -> None:
    ws, config = workspace
    outcome, run = _run(ws, config)
    assert outcome.card_count >= 3
    card_files = [path for path in ws.card_files() if path.name != "index.md"]
    assert len(card_files) >= 4
    assert (ws.cards_dir / "index.md").is_file()
    assert (ws.cards_dir / f"{config.project_id}.md").is_file()
    for name in ("sources.md", "open_questions.md", "conflicts.md", "manual_review.md", "validation.md"):
        assert (ws.reports_dir / name).is_file(), name
    card_text = (ws.cards_dir / "marta-toplog-rc-documents.md").read_text(encoding="utf-8")
    assert "review_status: needs_review" in card_text
    assert "review_note:" in card_text
    assert "## Источники" in card_text
    assert "doc-001:b-" in card_text or "doc-002:b-" in card_text
    assert run.manifest.status is RunStatus.completed
    assert run.manifest.input_tokens > 0
    assert (run.run_dir / "changes.diff").is_file()
    assert (run.run_dir / "inventory.json").is_file()
    assert (run.run_dir / "outline.json").is_file()
    assert (run.run_dir / "facts.json").is_file()


def test_repeat_run_with_same_inputs_is_stable(workspace: tuple[Workspace, ProjectConfig]) -> None:
    ws, config = workspace
    _run(ws, config)
    first = {
        path.relative_to(ws.root).as_posix(): path.read_text(encoding="utf-8")
        for path in ws.card_files()
    }
    first_reports = {
        path.name: path.read_text(encoding="utf-8")
        for path in ws.reports_dir.glob("*.md")
    }
    _run(ws, config)
    second = {
        path.relative_to(ws.root).as_posix(): path.read_text(encoding="utf-8")
        for path in ws.card_files()
    }
    second_reports = {
        path.name: path.read_text(encoding="utf-8")
        for path in ws.reports_dir.glob("*.md")
    }
    assert first == second
    assert first_reports == second_reports


def test_dry_run_writes_no_cards(workspace: tuple[Workspace, ProjectConfig]) -> None:
    ws, config = workspace
    outcome, run = _run(ws, config, dry_run=True)
    assert outcome.dry_run
    assert not list(ws.cards_dir.glob("*.md"))
    assert not list(ws.reports_dir.glob("*.md"))
    assert (run.run_dir / "staged" / "cards").is_dir()
    assert (run.run_dir / "changes.diff").is_file()
