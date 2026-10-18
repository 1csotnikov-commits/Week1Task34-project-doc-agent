"""Парсер TXT."""

from __future__ import annotations

import re

from project_doc_agent.ingest.common import BlockBuilder, ParseOutput
from project_doc_agent.models import ManualReviewItem
from project_doc_agent.utils import normalize_ws

NUMBERED_HEADING_RE = re.compile(r"^\d+(?:\.\d+)*\.?\s+\S")


def parse_txt(text: str, document_id: str, file: str, detect_headings: bool = False) -> ParseOutput:
    output = ParseOutput()
    builder = BlockBuilder(document_id, file)
    paragraphs: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            paragraphs.append("\n".join(buffer))
            buffer.clear()

    for line in text.splitlines():
        if line.strip():
            buffer.append(line.rstrip())
        else:
            flush()
    flush()

    stack: list[tuple[int, str]] = []

    def current_path() -> list[str]:
        return [title for _, title in stack]

    for paragraph in paragraphs:
        collapsed = normalize_ws(paragraph)
        if not collapsed:
            continue
        if detect_headings and len(collapsed) <= 120 and NUMBERED_HEADING_RE.match(collapsed):
            number = re.match(r"^(\d+(?:\.\d+)*)", collapsed)
            level = len(number.group(1).split(".")) if number else 1
            stack = [item for item in stack if item[0] < level]
            stack.append((level, collapsed))
            builder.add("heading", collapsed, current_path(), level=level)
            continue
        if "\n" in paragraph and all(
            line.lstrip().startswith(("-", "*", "+")) or re.match(r"^\s*\d+[.)]\s", line)
            for line in paragraph.splitlines()
            if line.strip()
        ):
            builder.add("list", paragraph.strip(), current_path())
        else:
            builder.add("paragraph", collapsed, current_path())

    output.blocks = builder.blocks
    if output.blocks:
        output.title = file.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    else:
        output.warnings.append(f"В {file} не найдено текстового содержимого")
        output.manual_review.append(ManualReviewItem(file=file, reason="Пустой TXT-документ", kind="other"))
    return output
