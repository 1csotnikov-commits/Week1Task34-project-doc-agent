"""Парсер Markdown."""

from __future__ import annotations

import re

import yaml

from project_doc_agent.ingest.common import BlockBuilder, ParseOutput
from project_doc_agent.models import ManualReviewItem
from project_doc_agent.utils import normalize_ws

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})\s*([A-Za-z0-9_+-]*)\s*$")
LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S")
TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")


def _front_matter(lines: list[str]) -> tuple[dict[str, object], int]:
    if not lines or lines[0].strip() != "---":
        return {}, 0
    for index in range(1, len(lines)):
        if lines[index].strip() in {"---", "..."}:
            raw = "\n".join(lines[1:index])
            try:
                data = yaml.safe_load(raw)
            except yaml.YAMLError:
                return {"front_matter_error": "invalid yaml"}, index + 1
            return (data if isinstance(data, dict) else {"front_matter": data}), index + 1
    return {}, 0


def _is_table_start(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    return "|" in lines[index] and bool(TABLE_SEPARATOR_RE.match(lines[index + 1]))


def parse_markdown(text: str, document_id: str, file: str) -> ParseOutput:
    output = ParseOutput()
    builder = BlockBuilder(document_id, file)
    lines = text.splitlines()
    metadata, start = _front_matter(lines)
    output.metadata = metadata
    title_value = metadata.get("title")
    if isinstance(title_value, str) and title_value.strip():
        output.title = title_value.strip()

    stack: list[tuple[int, str]] = []

    def current_path() -> list[str]:
        return [title for _, title in stack]

    index = start
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            index += 1
            continue
        if stripped.startswith("<!--"):
            while index < len(lines) and "-->" not in lines[index]:
                index += 1
            index += 1
            continue
        fence = FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)
            language = fence.group(2)
            index += 1
            code_lines: list[str] = []
            while index < len(lines) and not lines[index].strip().startswith(marker):
                code_lines.append(lines[index])
                index += 1
            index += 1
            code = "\n".join(code_lines)
            if code.strip():
                builder.add("code", code, current_path(), metadata={"language": language})
            continue
        heading = HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            heading_text = normalize_ws(heading.group(2))
            stack = [item for item in stack if item[0] < level]
            stack.append((level, heading_text))
            builder.add("heading", heading_text, current_path(), level=level)
            if level == 1 and not output.title:
                output.title = heading_text
            index += 1
            continue
        if _is_table_start(lines, index):
            table_lines = [line.rstrip()]
            index += 1
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                table_lines.append(lines[index].rstrip())
                index += 1
            builder.add("table", "\n".join(table_lines), current_path(), is_table=True)
            output.tables += 1
            continue
        if LIST_RE.match(line):
            list_lines: list[str] = []
            while index < len(lines):
                candidate = lines[index]
                if not candidate.strip():
                    if list_lines and index + 1 < len(lines) and (
                        LIST_RE.match(lines[index + 1]) or lines[index + 1].startswith((" ", "\t"))
                    ):
                        list_lines.append("")
                        index += 1
                        continue
                    break
                if LIST_RE.match(candidate) or candidate.startswith((" ", "\t")):
                    list_lines.append(candidate.rstrip())
                    index += 1
                    continue
                break
            list_text = "\n".join(list_lines).strip()
            if list_text:
                builder.add("list", list_text, current_path())
            continue
        paragraph: list[str] = []
        while index < len(lines):
            candidate = lines[index]
            if not candidate.strip():
                break
            if (
                HEADING_RE.match(candidate)
                or FENCE_RE.match(candidate)
                or LIST_RE.match(candidate)
                or _is_table_start(lines, index)
                or candidate.strip().startswith("<!--")
            ):
                break
            paragraph.append(candidate.strip())
            index += 1
        paragraph_text = normalize_ws(" ".join(paragraph))
        if paragraph_text:
            builder.add("paragraph", paragraph_text, current_path())

    output.blocks = builder.blocks
    output.tables = builder.table_index
    if output.blocks and output.title is None:
        output.title = file.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    if not output.blocks:
        output.warnings.append(f"В {file} не найдено текстового содержимого")
        output.manual_review.append(
            ManualReviewItem(file=file, reason="Пустой Markdown-документ", kind="other")
        )
    return output
