"""Тесты поиска."""

from __future__ import annotations

from project_doc_agent.config import ProjectConfig
from project_doc_agent.index.fts import (
    SearchIndex,
    card_records,
    render_search_report,
    save_search_report,
    source_records,
)
from project_doc_agent.ingest.inventory import build_inventory
from project_doc_agent.runs.manifest import RunContext
from project_doc_agent.workspace import Workspace


def _records(ws: Workspace, config: ProjectConfig):
    source = config.source_path(ws.root)
    inventory = build_inventory(source)
    records, _ = source_records(source, inventory.documents, config)
    return records


def test_search_finds_method_in_two_files(workspace: tuple[Workspace, ProjectConfig]) -> None:
    ws, config = workspace
    records = _records(ws, config)
    index = SearchIndex(ws)
    result = index.search(records, "createShipmentOrder", context=1)
    assert len(result.matches) >= 2
    assert len(result.files) >= 2
    assert all(match.block_id for match in result.matches)
    assert all(match.heading_path for match in result.matches)
    report = render_search_report(result)
    assert "createShipmentOrder" in report
    assert "## " in report


def test_search_regex_and_context(workspace: tuple[Workspace, ProjectConfig]) -> None:
    ws, config = workspace
    records = _records(ws, config)
    result = SearchIndex(ws).search(records, r"create(Shipment)?Order", regex=True, context=2)
    assert result.matches
    assert all(match.snippet for match in result.matches)


def test_search_saves_matches_and_report(workspace: tuple[Workspace, ProjectConfig]) -> None:
    ws, config = workspace
    records = _records(ws, config)
    run = RunContext.create(ws, "test-search")
    result = SearchIndex(ws).search(records, "Номенклатура", context=0)
    report = save_search_report(ws, run, result)
    assert report.is_file()
    assert (run.run_dir / "matches.json").is_file()
    assert "matches" in (run.run_dir / "matches.json").read_text(encoding="utf-8")


def test_card_records_are_searchable(workspace: tuple[Workspace, ProjectConfig]) -> None:
    ws, config = workspace
    card_path = ws.cards_dir / "marta-toplog-rc-demo.md"
    card_path.write_text(
        """---
id: marta-toplog-rc-demo
client: marta
client_name: ООО
project: toplog-rc
project_name: TopLog
bases: [ut11-prod]
title: Демо
type: other
profile: integration
status: draft
risk: low
sources:
  - file: alpha.md
    section: Раздел
    block_id: doc-001:b-001
review_status: ok
tags: [demo]
created: "2026-10-18"
updated: "2026-10-18"
---

# Демо

## Назначение

УникальныйМаркерДляПоиска.
""",
        encoding="utf-8",
    )
    records = card_records(ws, config)
    assert records
    result = SearchIndex(ws).search(records, "УникальныйМаркерДляПоиска", context=0)
    assert result.matches
    assert result.matches[0].origin == "card"
