"""CLI-тесты."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from project_doc_agent.cli import app
from tests.fakes import FakeProvider

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "docagent" in result.output


def test_init_non_interactive_and_projects(tmp_path: Path, sample_source: Path) -> None:
    root = tmp_path / "newproject"
    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(root),
            "--client",
            "marta",
            "--client-name",
            "ООО «Марта»",
            "--project",
            "toplog-rc",
            "--project-name",
            "TopLog",
            "--base",
            "ut11-prod",
            "--profile",
            "integration",
            "--provider",
            "deepseek",
            "--source-dir",
            str(sample_source),
            "--non-interactive",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (root / "docagent.yaml").is_file()
    assert (root / "cards").is_dir()
    assert (root / "reports").is_dir()

    listing = runner.invoke(app, ["projects", "--root", str(tmp_path)])
    assert listing.exit_code == 0, listing.output
    assert "toplog-rc" in listing.output
    assert "ООО «Марта»" in listing.output


def test_init_interactive(tmp_path: Path, sample_source: Path) -> None:
    root = tmp_path / "interactive"
    inputs = "\n".join(
        [
            "marta",
            "ООО «Марта»",
            "toplog-rc",
            "Интеграция TopLog",
            "ut11-prod,ut11-test",
            "integration",
            "deepseek",
            str(sample_source),
        ]
    ) + "\n"
    result = runner.invoke(app, ["init", "--workspace", str(root)], input=inputs)
    assert result.exit_code == 0, result.output
    assert (root / "docagent.yaml").is_file()
    content = (root / "docagent.yaml").read_text(encoding="utf-8")
    assert "ut11-test" in content
    assert "toplog-rc" in content


def test_cli_ingest_search_validate_run(workspace, monkeypatch) -> None:
    ws, config = workspace
    monkeypatch.setattr("project_doc_agent.cli.build_provider", lambda *args, **kwargs: FakeProvider())

    ingest = runner.invoke(app, ["ingest", "--workspace", str(ws.root)])
    assert ingest.exit_code == 0, ingest.output
    assert "Карточек:" in ingest.output
    assert list(ws.cards_dir.glob("*.md"))

    search = runner.invoke(
        app,
        [
            "search",
            "--workspace",
            str(ws.root),
            "--query",
            "createShipmentOrder",
            "--context",
            "2",
            "--include-cards",
        ],
    )
    assert search.exit_code == 0, search.output
    assert "Найдено совпадений" in search.output
    assert (ws.reports_dir / "search_report.md").is_file()

    validate = runner.invoke(app, ["validate", "--workspace", str(ws.root), "--diff"])
    assert validate.exit_code == 0, validate.output
    assert "changes.diff" in validate.output

    run = runner.invoke(
        app,
        [
            "run",
            "--workspace",
            str(ws.root),
            "--goal",
            "Разбери документы и собери документацию",
        ],
    )
    assert run.exit_code == 0, run.output
    assert "Шагов:" in run.output
