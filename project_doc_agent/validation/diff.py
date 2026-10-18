"""Unified diff: генерация и применение."""

from __future__ import annotations

import difflib
import re
from pathlib import Path

from project_doc_agent.errors import DiffApplyError
from project_doc_agent.workspace import Workspace

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def make_diff(relative_path: str, old: str, new: str) -> str:
    if old == new:
        return ""
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{relative_path}",
        tofile=f"b/{relative_path}",
        lineterm="\n",
    )
    return "".join(line if line.endswith("\n") else line + "\n" for line in diff)


def make_multi_diff(changes: dict[str, tuple[str, str]]) -> str:
    parts: list[str] = []
    for relative_path in sorted(changes):
        old, new = changes[relative_path]
        diff = make_diff(relative_path, old, new)
        if diff:
            parts.append(diff)
    return "\n".join(parts)


def apply_unified_diff(original: str, diff: str) -> str:
    if not diff.strip():
        return original
    original_lines = original.splitlines(keepends=True)
    diff_lines = diff.splitlines(keepends=True)
    result: list[str] = []
    cursor = 0
    index = 0
    while index < len(diff_lines):
        line = diff_lines[index]
        if line.startswith(("--- ", "+++ ")):
            index += 1
            continue
        match = HUNK_RE.match(line)
        if match is None:
            index += 1
            continue
        old_start = int(match.group(1))
        old_count = int(match.group(2) or "1")
        start = max(0, old_start - 1) if old_count else old_start
        if start < cursor or start > len(original_lines):
            raise DiffApplyError(f"Некорректная позиция hunk: {line.strip()}")
        result.extend(original_lines[cursor:start])
        cursor = start
        index += 1
        while index < len(diff_lines) and not HUNK_RE.match(diff_lines[index]):
            hunk_line = diff_lines[index]
            if hunk_line.startswith("\\"):
                index += 1
                continue
            prefix = hunk_line[:1]
            content = hunk_line[1:]
            if prefix == " ":
                if cursor >= len(original_lines) or original_lines[cursor].rstrip("\r\n") != content.rstrip("\r\n"):
                    raise DiffApplyError(f"Контекст не совпадает в строке {cursor + 1}")
                result.append(original_lines[cursor])
                cursor += 1
            elif prefix == "-":
                if cursor >= len(original_lines) or original_lines[cursor].rstrip("\r\n") != content.rstrip("\r\n"):
                    raise DiffApplyError(f"Удаляемая строка не совпадает в {cursor + 1}")
                cursor += 1
            elif prefix == "+":
                result.append(content)
            elif content.strip() == "" and prefix == "":
                result.append("\n")
            else:
                raise DiffApplyError(f"Неизвестная строка diff: {hunk_line!r}")
            index += 1
    result.extend(original_lines[cursor:])
    return "".join(result)


def parse_multi_diff(diff: str) -> dict[str, str]:
    """Разбить multi-file diff на файлы: relative_path -> diff."""
    files: dict[str, str] = {}
    current_path: str | None = None
    current_lines: list[str] = []
    for line in diff.splitlines(keepends=True):
        if line.startswith("--- a/"):
            if current_path and current_lines:
                files[current_path] = "".join(current_lines)
            current_path = line[len("--- a/") :].strip()
            current_lines = [line]
        elif line.startswith("+++ b/"):
            if current_path:
                current_lines.append(line)
        elif current_path:
            current_lines.append(line)
    if current_path and current_lines:
        files[current_path] = "".join(current_lines)
    return files


def apply_multi_diff(workspace: Workspace, diff: str) -> list[Path]:
    applied: list[Path] = []
    for relative, file_diff in parse_multi_diff(diff).items():
        if ".." in Path(relative).parts or Path(relative).is_absolute():
            raise DiffApplyError(f"Недопустимый путь в diff: {relative}")
        target = workspace.resolve_write(workspace.root / relative)
        if not target.is_file():
            raise DiffApplyError(f"Файл для применения diff не найден: {relative}")
        original = target.read_text(encoding="utf-8")
        updated = apply_unified_diff(original, file_diff)
        workspace.atomic_write(relative, updated)
        applied.append(target)
    return applied
