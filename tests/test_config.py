"""Тесты конфигурации."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_doc_agent.config import ProjectConfig, load_project_config, load_providers, make_project_config
from project_doc_agent.errors import ConfigError, WorkspaceError
from project_doc_agent.workspace import Workspace


def test_load_project_config(workspace: tuple[Workspace, ProjectConfig]) -> None:
    ws, _ = workspace
    config = load_project_config(ws.root)
    assert config.client == "marta"
    assert config.project == "toplog-rc"
    assert config.bases == ["ut11-prod"]
    assert config.obsidian_folder == "marta/toplog-rc/cards"
    assert config.profile.value == "integration"


def test_invalid_slug_and_paths(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ProjectConfig(
            client="Марта",
            client_name="ООО",
            project="toplog",
            project_name="TopLog",
            bases=["ut11"],
            source_dir=str(tmp_path),
        )
    with pytest.raises(ValueError):
        ProjectConfig(
            client="marta",
            client_name="ООО",
            project="toplog",
            project_name="TopLog",
            bases=[],
            source_dir=str(tmp_path),
        )
    with pytest.raises(ValueError):
        ProjectConfig(
            client="marta",
            client_name="ООО",
            project="toplog",
            project_name="TopLog",
            bases=["ut11"],
            source_dir=str(tmp_path),
            output_dir="../outside",
        )


def test_missing_source(tmp_path: Path) -> None:
    config = make_project_config(
        client="marta",
        client_name="ООО",
        project="toplog",
        project_name="TopLog",
        bases=["ut11"],
        source_dir=str(tmp_path / "missing"),
    )
    from project_doc_agent.config import save_project_config

    save_project_config(tmp_path, config)
    with pytest.raises(ConfigError):
        load_project_config(tmp_path)


def test_providers_config_has_three_providers() -> None:
    providers = load_providers()
    names = {provider.name for provider in providers.providers}
    assert names == {"deepseek", "openrouter", "mistral"}
    assert providers.by_name("deepseek").base_url.startswith("https://api.deepseek.com")


def test_workspace_custom_output_dirs(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "project")
    ws.configure("docs/cards", "docs/reports")
    ws.ensure_layout()
    target = ws.atomic_write("docs/cards/card.md", "text")
    assert target.is_file()
    assert target.parent == ws.cards_dir
    with pytest.raises(WorkspaceError):
        ws.atomic_write("cards/card.md", "text")
