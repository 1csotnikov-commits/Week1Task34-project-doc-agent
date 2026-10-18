"""Инструменты агентного режима и ограничения безопасности."""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from project_doc_agent.config import ProjectConfig
from project_doc_agent.errors import PlanError, WorkspaceError
from project_doc_agent.extract.facts import extract_all
from project_doc_agent.index.fts import SearchIndex, SearchRecord
from project_doc_agent.ingest.chunking import chunk_document
from project_doc_agent.ingest.docx_parser import parse_docx
from project_doc_agent.ingest.inventory import build_inventory
from project_doc_agent.ingest.outline import build_outline
from project_doc_agent.ingest.parser import ParsedCorpus, parse_corpus
from project_doc_agent.models import Card, Fact, ToolCallRecord
from project_doc_agent.providers.base import LLMProvider
from project_doc_agent.runs.manifest import RunContext
from project_doc_agent.utils import normalize_ws, truncate
from project_doc_agent.validation.cards import split_front_matter
from project_doc_agent.validation.diff import apply_unified_diff, make_multi_diff
from project_doc_agent.validation.validator import validate_workspace
from project_doc_agent.workspace import Workspace


class ListFilesArgs(BaseModel):
    glob: str = "*"


class ReadFileArgs(BaseModel):
    path: str
    max_chars: int = Field(default=8000, ge=100, le=50_000)


class ReadDocxArgs(BaseModel):
    path: str
    max_blocks: int = Field(default=200, ge=1, le=2000)


class OutlineArgs(BaseModel):
    path: str | None = None


class SearchArgs(BaseModel):
    query: str
    regex: bool = False
    context: int = Field(default=1, ge=0, le=10)


class ExtractArgs(BaseModel):
    force: bool = False


class WriteCardArgs(BaseModel):
    filename: str = ""
    content: str


class WriteFileArgs(BaseModel):
    path: str
    content: str


class ApplyPatchArgs(BaseModel):
    path: str
    diff: str


class MakeDiffArgs(BaseModel):
    old: str
    new: str


class ValidateArgs(BaseModel):
    pass


class DiffArgs(BaseModel):
    pass


@dataclass
class ToolResult:
    ok: bool
    output: str = ""
    error: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[..., ToolResult]


class ToolLayer:
    def __init__(
        self,
        *,
        workspace: Workspace,
        config: ProjectConfig,
        source_dir: Path,
        provider: LLMProvider,
        run: RunContext,
        dry_run: bool = False,
    ) -> None:
        self.workspace = workspace
        self.config = config
        self.source_dir = source_dir.resolve()
        self.provider = provider
        self.run = run
        self.dry_run = dry_run
        self.staged: dict[str, str] = {}
        self.facts: list[Fact] = []
        self._corpus: ParsedCorpus | None = None
        self._specs = self._build_specs()

    def _build_specs(self) -> dict[str, ToolSpec]:
        return {
            "list_files": ToolSpec("list_files", "Список файлов источников", ListFilesArgs, self.list_files),
            "read_file": ToolSpec("read_file", "Чтение текстового источника", ReadFileArgs, self.read_file),
            "read_docx": ToolSpec("read_docx", "Чтение DOCX", ReadDocxArgs, self.read_docx),
            "outline": ToolSpec("outline", "Оглавление корпуса", OutlineArgs, self.outline),
            "search": ToolSpec("search", "Поиск по источникам", SearchArgs, self.search),
            "extract": ToolSpec("extract", "Извлечение фактов", ExtractArgs, self.extract),
            "write_card": ToolSpec("write_card", "Запись карточки", WriteCardArgs, self.write_card),
            "write_file": ToolSpec("write_file", "Запись файла", WriteFileArgs, self.write_file),
            "apply_patch": ToolSpec("apply_patch", "Применение patch", ApplyPatchArgs, self.apply_patch),
            "make_diff": ToolSpec("make_diff", "Создание diff", MakeDiffArgs, self.make_diff),
            "validate": ToolSpec("validate", "Валидация карточек", ValidateArgs, self.validate),
            "diff": ToolSpec("diff", "Diff подготовленных изменений", DiffArgs, self.diff),
        }

    @property
    def available_tools(self) -> list[str]:
        return sorted(self._specs)

    def json_schema(self) -> dict[str, Any]:
        return {
            name: {
                "description": spec.description,
                "arguments": spec.args_model.model_json_schema(),
            }
            for name, spec in sorted(self._specs.items())
        }

    def execute(self, name: str, args: dict[str, Any], step: int = 0) -> ToolResult:
        spec = self._specs.get(name)
        if spec is None:
            raise PlanError(f"Неизвестный инструмент: {name!r}. Доступны: {', '.join(self.available_tools)}")
        started = time.perf_counter()
        try:
            parsed = spec.args_model.model_validate(args)
            result = spec.handler(**parsed.model_dump())
        except PlanError:
            raise
        except ValidationError as exc:
            result = ToolResult(ok=False, error=f"Неверные аргументы {name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - инструмент не должен ронять прогон
            result = ToolResult(ok=False, error=f"{name}: {exc}")
        duration_ms = int((time.perf_counter() - started) * 1000)
        self.run.add_tool_call(
            ToolCallRecord(
                step=step,
                tool=name,
                args=_sanitize_args(args),
                ok=result.ok,
                duration_ms=duration_ms,
                error=result.error,
            )
        )
        return result

    def _resolve_source(self, path: str) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.source_dir / candidate
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(self.source_dir):
            raise WorkspaceError(f"Чтение вне source_dir запрещено: {path}")
        if not resolved.is_file():
            raise WorkspaceError(f"Файл не найден: {path}")
        return resolved

    def _resolve_readable(self, path: str) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace.root / candidate
        resolved = candidate.resolve(strict=False)
        allowed = (self.source_dir, self.workspace.cards_dir, self.workspace.reports_dir)
        if not any(resolved.is_relative_to(root.resolve()) for root in allowed):
            raise WorkspaceError(f"Чтение вне разрешённых каталогов запрещено: {path}")
        if resolved.name == ".env" or resolved.suffix in {".key", ".pem"}:
            raise WorkspaceError("Чтение секретов запрещено")
        if not resolved.is_file():
            raise WorkspaceError(f"Файл не найден: {path}")
        return resolved

    def _stage_path(self, path: str, *, default_dir: str | None = None) -> str:
        raw = Path(path)
        if raw.is_absolute() or ".." in raw.parts:
            raise WorkspaceError(f"Недопустимый путь записи: {path}")
        relative = raw.as_posix()
        if default_dir and not any(relative.startswith(f"{root}/") for root in ("cards", "reports", self.config.output_dir)):
            relative = f"{default_dir}/{raw.name}"
        resolved = self.workspace.resolve_write(self.workspace.root / relative)
        return self.workspace.relative(resolved)

    def _load_corpus(self) -> ParsedCorpus:
        if self._corpus is not None:
            return self._corpus
        inventory = build_inventory(self.source_dir, max_file_size_mb=self.config.max_file_size_mb)
        self._corpus = parse_corpus(self.source_dir, inventory.documents, self.config)
        return self._corpus


    # ---- инструменты ----

    def list_files(self, glob: str = "*") -> ToolResult:
        pattern = glob.strip() or "*"
        if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
            raise WorkspaceError(f"Недопустимый glob: {glob}")
        if "/" not in pattern and not pattern.startswith("**"):
            pattern = f"**/{pattern}"
        files = []
        for path in self.source_dir.glob(pattern):
            if not path.is_file() or path.is_symlink():
                continue
            relative_parts = path.relative_to(self.source_dir).parts
            if any(part.startswith(".") for part in relative_parts):
                continue
            if path.name.startswith("~$"):
                continue
            files.append(path.relative_to(self.source_dir).as_posix())
        files = sorted(files, key=str.casefold)[:500]
        return ToolResult(ok=True, output="\n".join(files) or "(файлы не найдены)", data={"files": files})

    def read_file(self, path: str, max_chars: int = 8000) -> ToolResult:
        resolved = self._resolve_source(path)
        if resolved.suffix.lower() == ".docx":
            delegated = self.read_docx(path, max_blocks=200)
            if not delegated.ok:
                return delegated
            return ToolResult(
                ok=True,
                output=truncate(delegated.output, max_chars),
                data=delegated.data,
            )
        text, encoding = self.workspace.read_text(resolved, max_bytes=self.config.max_file_size_mb * 1024 * 1024)
        relative = resolved.relative_to(self.source_dir).as_posix()
        return ToolResult(
            ok=True,
            output=truncate(text, max_chars),
            data={"path": relative, "encoding": encoding},
        )

    def read_docx(self, path: str, max_blocks: int = 200) -> ToolResult:
        resolved = self._resolve_source(path)
        if resolved.suffix.lower() != ".docx":
            return ToolResult(ok=False, error="read_docx принимает только .docx")
        relative = resolved.relative_to(self.source_dir).as_posix()
        parsed = parse_docx(resolved, "tool", relative)
        lines = [f"Файл: {relative}", f"Заголовок: {parsed.title or '(нет)'}", ""]
        for block in parsed.blocks[:max_blocks]:
            lines.append(f"[{block.block_id}] {block.kind} | {' / '.join(block.heading_path) or '(без раздела)'}")
            lines.append(truncate(block.text, 1200))
            lines.append("")
        if parsed.manual_review:
            lines.append("Ручная проверка:")
            lines.extend(f"- {item.reason}" for item in parsed.manual_review)
        return ToolResult(
            ok=True,
            output=truncate("\n".join(lines), 20_000),
            data={"blocks": len(parsed.blocks), "tables": parsed.tables, "images": parsed.images},
        )

    def outline(self, path: str | None = None) -> ToolResult:
        corpus = self._load_corpus()
        documents = corpus.documents
        blocks = corpus.blocks
        if path:
            resolved = self._resolve_source(path)
            relative = resolved.relative_to(self.source_dir).as_posix()
            documents = [document for document in documents if document.file == relative]
            if not documents:
                return ToolResult(ok=False, error=f"Документ не найден в корпусе: {path}")
            blocks = [block for block in blocks if block.file == relative]
        outline = build_outline(documents, blocks)
        summary = {
            "totals": outline["totals"],
            "documents": [
                {
                    "file": document["file"],
                    "title": document["title"],
                    "headings": len(document["headings"]),
                    "tables": document["table_count"],
                    "warnings": document["warnings"],
                }
                for document in outline["documents"]
            ],
            "logical_units": outline["logical_units"],
        }
        return ToolResult(
            ok=True,
            output=truncate(json.dumps(summary, ensure_ascii=False, indent=2), 15_000),
            data={"totals": outline["totals"]},
        )

    def search(self, query: str, regex: bool = False, context: int = 1) -> ToolResult:
        corpus = self._load_corpus()
        records = [
            SearchRecord(
                document_id=block.document_id,
                file=block.file,
                origin="source",
                block_id=block.block_id,
                kind=block.kind,
                heading_path=block.heading_path,
                block_index=block.block_index,
                table_index=block.table_index,
                text=block.text,
            )
            for block in corpus.blocks
        ]
        result = SearchIndex(self.workspace).search(
            records, query, regex=regex, context=context, include_cards=False
        )
        matches = [
            {
                "number": match.number,
                "file": match.file,
                "section": match.section,
                "block_id": match.block_id,
                "kind": match.kind,
                "snippet": truncate(normalize_ws(match.snippet), 600),
            }
            for match in result.matches[:20]
        ]
        return ToolResult(
            ok=True,
            output=truncate(json.dumps(
                {"total": len(result.matches), "matches": matches}, ensure_ascii=False, indent=2
            ), 15_000),
            data={"total": len(result.matches)},
        )


    def extract(self, force: bool = False) -> ToolResult:
        if self.facts and not force:
            return ToolResult(
                ok=True,
                output=f"Факты уже извлечены: {len(self.facts)}",
                data={"facts": len(self.facts), "cached": True},
            )
        corpus = self._load_corpus()
        chunks = []
        for document in corpus.documents:
            blocks = [block for block in corpus.blocks if block.document_id == document.document_id]
            chunks.extend(
                chunk_document(
                    document.document_id,
                    document.file,
                    blocks,
                    max_chars=self.config.max_chunk_chars,
                    document_sha=document.sha256,
                )
            )
        extraction = extract_all(
            chunks=chunks,
            blocks=corpus.blocks,
            provider=self.provider,
            run=self.run,
            config=self.config,
            goal=self.run.manifest.goal or "",
        )
        self.facts = extraction.facts
        self.run.write_json(
            "tool_facts.json",
            [fact.model_dump(mode="json") for fact in self.facts],
        )
        return ToolResult(
            ok=not extraction.skipped_chunks and bool(self.facts),
            output=f"Извлечено фактов: {len(self.facts)}; пропущено чанков: {len(extraction.skipped_chunks)}",
            error="; ".join(extraction.warnings[-3:]) if extraction.skipped_chunks else None,
            data={"facts": len(self.facts), "skipped_chunks": len(extraction.skipped_chunks)},
        )

    def write_card(self, filename: str = "", content: str = "") -> ToolResult:
        if len(content) > 2_000_000:
            return ToolResult(ok=False, error="Карточка слишком большая (>2 МБ)")
        data, body, error = split_front_matter(content)
        if error:
            return ToolResult(ok=False, error=f"Некорректный front matter: {error}")
        try:
            card = Card.model_validate({**data, "body": body})
        except ValidationError as exc:
            return ToolResult(ok=False, error=f"Карточка не прошла схему: {truncate(str(exc), 1500)}")
        name = filename.strip() or f"{card.id}.md"
        if not name.endswith(".md"):
            name += ".md"
        relative = self._stage_path(f"{self.config.output_dir}/{name}")
        self.staged[relative] = content
        return ToolResult(
            ok=True,
            output=(
                f"Карточка подготовлена: {relative}"
                + (" (dry-run, не записана)" if self.dry_run else "")
            ),
            data={"path": relative, "id": card.id},
        )

    def write_file(self, path: str, content: str = "") -> ToolResult:
        if len(content) > 2_000_000:
            return ToolResult(ok=False, error="Файл слишком большой (>2 МБ)")
        relative = self._stage_path(path)
        if not relative.endswith((".md", ".txt", ".json")):
            return ToolResult(ok=False, error="Разрешены только .md, .txt, .json")
        self.staged[relative] = content
        return ToolResult(ok=True, output=f"Подготовлен файл: {relative}", data={"path": relative})

    def apply_patch(self, path: str, diff: str = "") -> ToolResult:
        relative = self._stage_path(path)
        current = self.staged.get(relative)
        if current is None:
            target = self.workspace.resolve_read(relative)
            if not target.is_file():
                return ToolResult(ok=False, error=f"Файл не найден: {relative}")
            current = target.read_text(encoding="utf-8")
        try:
            updated = apply_unified_diff(current, diff)
        except Exception as exc:  # noqa: BLE001 - diff приходит извне
            return ToolResult(ok=False, error=f"Patch не применён: {exc}")
        self.staged[relative] = updated
        return ToolResult(ok=True, output=f"Patch применён к {relative}", data={"path": relative})

    def make_diff(self, old: str, new: str) -> ToolResult:
        diff = make_multi_diff({"inline.md": (old, new)})
        return ToolResult(ok=True, output=diff or "(изменений нет)", data={})


    def validate(self) -> ToolResult:
        preview = self._prepare_preview()
        result = validate_workspace(preview, self.config, apply=False)
        issues = [
            {
                "severity": issue.severity,
                "code": issue.code,
                "card": issue.card_id,
                "message": issue.message,
            }
            for issue in result.issues[:40]
        ]
        output = json.dumps(
            {
                "cards_checked": result.cards_checked,
                "errors": len(result.errors),
                "warnings": len(result.warnings),
                "issues": issues,
            },
            ensure_ascii=False,
            indent=2,
        )
        return ToolResult(
            ok=not result.errors,
            output=truncate(output, 12_000),
            error=None if not result.errors else f"Валидация нашла ошибок: {len(result.errors)}",
            data={"errors": len(result.errors), "warnings": len(result.warnings)},
        )

    def diff(self) -> ToolResult:
        changes: dict[str, tuple[str, str]] = {}
        for relative, content in sorted(self.staged.items()):
            target = self.workspace.resolve_read(relative)
            old = target.read_text(encoding="utf-8") if target.is_file() else ""
            if old != content:
                changes[relative] = (old, content)
        diff = make_multi_diff(changes)
        self.run.write_text("tool_changes.diff", diff)
        return ToolResult(
            ok=True,
            output=truncate(diff, 15_000) if diff else "(изменений нет)",
            data={"files": len(changes)},
        )

    def _prepare_preview(self) -> Workspace:
        preview_root = self.run.run_dir / "preview"
        if preview_root.exists():
            shutil.rmtree(preview_root)
        cards_target = preview_root / self.config.output_dir
        reports_target = preview_root / self.config.reports_dir
        if self.workspace.cards_dir.is_dir():
            shutil.copytree(self.workspace.cards_dir, cards_target, dirs_exist_ok=True)
        if self.workspace.reports_dir.is_dir():
            shutil.copytree(self.workspace.reports_dir, reports_target, dirs_exist_ok=True)
        cards_target.mkdir(parents=True, exist_ok=True)
        reports_target.mkdir(parents=True, exist_ok=True)
        for relative, content in self.staged.items():
            target = preview_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return Workspace(preview_root)


def _sanitize_args(args: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in args.items():
        if isinstance(value, str) and len(value) > 300:
            sanitized[key] = f"<{len(value)} chars>"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            sanitized[key] = value
        else:
            sanitized[key] = str(value)[:300]
    return sanitized

