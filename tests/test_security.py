"""Тесты безопасности путей."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from project_doc_agent.config import ProjectConfig
from project_doc_agent.errors import WorkspaceError
from project_doc_agent.workspace import Workspace


def test_write_outside_workspace_is_rejected(workspace: tuple[Workspace, ProjectConfig]) -> None:
    ws, _ = workspace
    with pytest.raises(WorkspaceError):
        ws.resolve_write("../outside.md")
    with pytest.raises(WorkspaceError):
        ws.resolve_write(Path("C:/Windows/evil.md"))
    with pytest.raises(WorkspaceError):
        ws.atomic_write("docagent.yaml", "hack")


def test_write_inside_cards_and_reports(workspace: tuple[Workspace, ProjectConfig]) -> None:
    ws, _ = workspace
    assert ws.atomic_write("cards/test.md", "ok").is_file()
    assert ws.atomic_write("reports/test.md", "ok").is_file()
    assert ws.atomic_write(".docagent/test.json", "{}").is_file()


def test_symlink_outside_is_rejected(workspace: tuple[Workspace, ProjectConfig], tmp_path: Path) -> None:
    ws, _ = workspace
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = ws.cards_dir / "link.md"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink недоступен без прав")
    with pytest.raises(WorkspaceError):
        ws.resolve_write("cards/link.md")
