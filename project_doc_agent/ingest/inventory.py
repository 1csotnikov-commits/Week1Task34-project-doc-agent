"""Инвентаризация исходных документов."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from project_doc_agent.errors import IngestError
from project_doc_agent.models import ManualReviewItem, SourceDocument
from project_doc_agent.utils import sha256_file

SUPPORTED_EXTENSIONS = {".docx", ".md", ".txt"}
IGNORED_DIRECTORIES = {".git", ".docagent", ".venv", "venv", "node_modules", "__pycache__"}


@dataclass
class InventoryResult:
    documents: list[SourceDocument] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    manual_review: list[ManualReviewItem] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _ignored(path: Path) -> bool:
    name = path.name
    if name.startswith("~$") or name.startswith(".~"):
        return True
    if name.startswith(".") and name not in {".env"}:
        return True
    return False


def iter_source_files(source_dir: Path) -> list[Path]:
    if not source_dir.exists():
        raise IngestError(f"Папка источников не существует: {source_dir}")
    if not source_dir.is_dir():
        raise IngestError(f"source_dir не является папкой: {source_dir}")
    files: list[Path] = []
    for path in source_dir.rglob("*"):
        try:
            relative_parts = path.relative_to(source_dir).parts
        except ValueError:
            continue
        if any(part in IGNORED_DIRECTORIES for part in relative_parts[:-1]):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        if _ignored(path):
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(source_dir).as_posix().casefold())


def build_inventory(source_dir: Path, max_file_size_mb: int = 50) -> InventoryResult:
    files = iter_source_files(source_dir)
    if not files:
        raise IngestError(
            f"В {source_dir} нет поддерживаемых файлов (.docx, .md, .txt). "
            "Проверьте source_dir в docagent.yaml."
        )
    result = InventoryResult()
    max_bytes = max_file_size_mb * 1024 * 1024
    for index, path in enumerate(files, start=1):
        relative = path.relative_to(source_dir).as_posix()
        document_id = f"doc-{index:03d}"
        try:
            stat = path.stat()
            if stat.st_size > max_bytes:
                reason = (
                    f"Файл {relative} ({stat.st_size} байт) превышает лимит "
                    f"{max_file_size_mb} МБ и не анализировался."
                )
                result.warnings.append(reason)
                result.manual_review.append(
                    ManualReviewItem(file=relative, reason=reason, kind="too_large")
                )
                result.skipped.append(relative)
                continue
            digest = sha256_file(path)
        except OSError as exc:
            reason = f"Не удалось прочитать {relative}: {exc}"
            result.warnings.append(reason)
            result.manual_review.append(
                ManualReviewItem(file=relative, reason=reason, kind="unreadable")
            )
            result.skipped.append(relative)
            continue
        modified = datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds")
        result.documents.append(
            SourceDocument(
                document_id=document_id,
                file=relative,
                ext=path.suffix.lower(),
                sha256=digest,
                size=stat.st_size,
                modified=modified,
            )
        )
    if not result.documents:
        raise IngestError("Ни один исходный файл не удалось прочитать. См. предупреждения.")
    return result
