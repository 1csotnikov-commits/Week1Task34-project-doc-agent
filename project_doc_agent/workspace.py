"""Workspace проекта и безопасная запись файлов."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterable
from pathlib import Path

from project_doc_agent.errors import WorkspaceError
from project_doc_agent.utils import sha256_file

ALLOWED_WRITE_DIRS = ("cards", "reports", ".docagent")
TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "cp1251")


class Workspace:
    """Каталог проекта с `docagent.yaml` и ограниченными зонами записи."""

    def __init__(
        self,
        root: Path,
        *,
        output_dir: str = "cards",
        reports_dir: str = "reports",
    ) -> None:
        self.root = root.expanduser().resolve()
        self._output_dir = output_dir
        self._reports_dir = reports_dir

    def configure(self, output_dir: str, reports_dir: str) -> None:
        for name in (output_dir, reports_dir):
            parts = Path(name).parts
            if Path(name).is_absolute() or ".." in parts or (parts and parts[0] == ".docagent"):
                raise WorkspaceError(f"Недопустимый каталог данных: {name}")
        self._output_dir = output_dir.replace("\\", "/")
        self._reports_dir = reports_dir.replace("\\", "/")

    @property
    def config_path(self) -> Path:
        return self.root / "docagent.yaml"

    @property
    def cards_dir(self) -> Path:
        return self.root / self._output_dir

    @property
    def reports_dir(self) -> Path:
        return self.root / self._reports_dir

    @property
    def docagent_dir(self) -> Path:
        return self.root / ".docagent"

    @property
    def runs_dir(self) -> Path:
        return self.docagent_dir / "runs"

    @property
    def cache_dir(self) -> Path:
        return self.docagent_dir / "cache"

    @property
    def index_dir(self) -> Path:
        return self.docagent_dir / "index"

    @classmethod
    def find(cls, start: Path | None = None) -> Workspace | None:
        current = (start or Path.cwd()).expanduser().resolve()
        if current.is_file():
            current = current.parent
        for candidate in (current, *current.parents):
            if (candidate / "docagent.yaml").is_file():
                return cls(candidate)
        return None

    @classmethod
    def require(cls, start: Path | None = None) -> Workspace:
        workspace = cls.find(start)
        if workspace is None:
            location = start or Path.cwd()
            raise WorkspaceError(
                f"Не найден docagent.yaml в {location} или выше. "
                "Создайте проект командой `docagent init`."
            )
        return workspace

    def ensure_layout(self) -> None:
        for directory in (
            self.root,
            self.cards_dir,
            self.reports_dir,
            self.docagent_dir,
            self.runs_dir,
            self.cache_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def relative(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError as exc:
            raise WorkspaceError(f"Путь вне workspace: {path}") from exc

    def allowed_roots(self) -> tuple[Path, ...]:
        return tuple(root.resolve() for root in (self.cards_dir, self.reports_dir, self.docagent_dir))

    def resolve_write(self, path: str | Path, allowed_roots: Iterable[Path] | None = None) -> Path:
        """Проверить путь записи и вернуть абсолютный путь внутри разрешённых зон."""
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve(strict=False)
        roots = tuple(root.resolve() for root in (allowed_roots or self.allowed_roots()))
        if not any(resolved.is_relative_to(root) for root in roots):
            raise WorkspaceError(f"Запись разрешена только в cards/, reports/ и .docagent/: {path}")
        if resolved == self.root:
            raise WorkspaceError("Нельзя перезаписывать корень workspace")
        return resolved

    def resolve_read(self, path: str | Path) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        return candidate.resolve(strict=False)

    def atomic_write(self, path: str | Path, content: str, allowed_roots: Iterable[Path] | None = None) -> Path:
        target = self.resolve_write(path, allowed_roots)
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(prefix=".docagent-", suffix=".tmp", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return target

    def read_text(self, path: str | Path, max_bytes: int | None = None) -> tuple[str, str]:
        """Прочитать текст, вернуть (содержимое, использованная кодировка)."""
        resolved = self.resolve_read(path)
        if max_bytes is not None and resolved.stat().st_size > max_bytes:
            raise WorkspaceError(f"Файл превышает лимит {max_bytes} байт: {resolved}")
        last_error: UnicodeDecodeError | None = None
        data = resolved.read_bytes()
        for encoding in TEXT_ENCODINGS:
            try:
                return data.decode(encoding), encoding
            except UnicodeDecodeError as exc:
                last_error = exc
        raise WorkspaceError(f"Не удалось определить кодировку {resolved}: {last_error}")

    def backup_file(self, path: str | Path, destination_dir: Path) -> Path:
        source = self.resolve_read(path)
        if not source.is_file():
            raise WorkspaceError(f"Нет файла для backup: {source}")
        try:
            relative = source.relative_to(self.root)
        except ValueError:
            relative = Path(source.name)
        destination = destination_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return destination

    def backup_tree(self, destination_dir: Path, roots: Iterable[Path] | None = None) -> list[Path]:
        backups: list[Path] = []
        for root in roots or (self.cards_dir, self.reports_dir):
            if not root.exists():
                continue
            for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold()):
                if path.is_file() and not path.is_symlink():
                    backups.append(self.backup_file(path, destination_dir))
        return backups

    def card_files(self) -> list[Path]:
        if not self.cards_dir.is_dir():
            return []
        return sorted(
            (path for path in self.cards_dir.rglob("*.md") if path.is_file() and not path.is_symlink()),
            key=lambda item: item.relative_to(self.cards_dir).as_posix().casefold(),
        )

    def write_staged(self, staged: dict[str, str], allowed_roots: Iterable[Path] | None = None) -> list[Path]:
        """Атомарно записать набор файлов (относительные POSIX-пути от корня)."""
        written: list[Path] = []
        for relative, content in sorted(staged.items()):
            written.append(self.atomic_write(relative, content, allowed_roots))
        return written

    def hash_file(self, path: str | Path) -> str:
        return sha256_file(self.resolve_read(path))

    def file_fingerprint(self, path: str | Path) -> dict[str, object]:
        resolved = self.resolve_read(path)
        stat = resolved.stat()
        return {
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": sha256_file(resolved),
        }
