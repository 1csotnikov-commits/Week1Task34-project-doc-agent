"""Прогоны (runs), манифест и состояние для resume."""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from project_doc_agent import __version__
from project_doc_agent.errors import WorkspaceError
from project_doc_agent.models import LLMCallRecord, RunManifest, RunStatus, ToolCallRecord
from project_doc_agent.resources import read_prompt
from project_doc_agent.utils import sha256_text, utc_now_iso
from project_doc_agent.workspace import Workspace


def new_run_id() -> str:
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(3)}"


def prompt_hashes(names: tuple[str, ...]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for name in names:
        content = read_prompt(name)
        hashes[name] = sha256_text(content)
    return hashes


class RunContext:
    def __init__(self, workspace: Workspace, run_id: str, manifest: RunManifest) -> None:
        self.workspace = workspace
        self.run_id = run_id
        self.manifest = manifest

    @property
    def run_dir(self) -> Path:
        return self.workspace.runs_dir / self.run_id

    @classmethod
    def create(
        cls,
        workspace: Workspace,
        command: str,
        *,
        provider: str = "",
        model: str = "",
        temperature: float = 0.1,
        max_tokens: int = 8192,
        goal: str | None = None,
        params: dict[str, Any] | None = None,
        prompt_names: tuple[str, ...] = (),
    ) -> RunContext:
        workspace.ensure_layout()
        run_id = new_run_id()
        run_dir = workspace.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        (run_dir / "snapshot").mkdir(exist_ok=True)
        manifest = RunManifest(
            run_id=run_id,
            command=command,
            started_at=utc_now_iso(),
            app_version=__version__,
            provider=provider,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            goal=goal,
            params=params or {},
            prompt_hashes=prompt_hashes(prompt_names) if prompt_names else {},
        )
        context = cls(workspace, run_id, manifest)
        context.save()
        return context

    @classmethod
    def open(cls, workspace: Workspace, run_id: str) -> RunContext:
        run_dir = workspace.runs_dir / run_id
        manifest_path = run_dir / "run.json"
        if not manifest_path.is_file():
            raise WorkspaceError(f"Прогон не найден: {run_id}")
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = RunManifest.model_validate(raw)
        if manifest.status is RunStatus.completed:
            raise WorkspaceError(f"Прогон {run_id} уже завершён")
        manifest.status = RunStatus.running
        manifest.finished_at = None
        manifest.errors = []
        context = cls(workspace, run_id, manifest)
        context.save()
        return context

    def save(self) -> Path:
        return self.write_json("run.json", self.manifest.model_dump(mode="json"))

    def write_json(self, name: str, payload: BaseModel | dict[str, Any] | list[Any]) -> Path:
        if isinstance(payload, BaseModel):
            data: Any = payload.model_dump(mode="json")
        else:
            data = payload
        content = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
        return self.workspace.atomic_write(self.run_dir / name, content)

    def read_json(self, name: str) -> Any:
        path = self.run_dir / name
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def write_text(self, name: str, content: str) -> Path:
        return self.workspace.atomic_write(self.run_dir / name, content)

    def add_llm_call(self, record: LLMCallRecord) -> None:
        self.manifest.add_llm_call(record)
        self.save()

    def add_tool_call(self, record: ToolCallRecord) -> None:
        self.manifest.tool_calls.append(record)
        self.save()

    def record_output(self, path: Path, relative: str | None = None) -> None:
        key = relative or self.workspace.relative(path)
        self.manifest.outputs[key] = self.workspace.hash_file(path)
        self.save()

    def finish(self, status: RunStatus, errors: list[str] | None = None) -> None:
        self.manifest.status = status
        self.manifest.finished_at = utc_now_iso()
        if errors:
            self.manifest.errors.extend(errors)
        self.save()
