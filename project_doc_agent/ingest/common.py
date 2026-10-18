"""Общие структуры парсеров документов."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from project_doc_agent.models import DocumentBlock, ManualReviewItem


@dataclass
class ParseOutput:
    blocks: list[DocumentBlock] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    manual_review: list[ManualReviewItem] = field(default_factory=list)
    title: str | None = None
    tables: int = 0
    images: int = 0


class BlockBuilder:
    """Нумерует блоки документа в стабильном порядке."""

    def __init__(self, document_id: str, file: str) -> None:
        self.document_id = document_id
        self.file = file
        self.blocks: list[DocumentBlock] = []
        self.table_index = 0
        self._index = 0

    def add(
        self,
        kind: Literal["heading", "paragraph", "list", "table", "code", "image"],
        text: str,
        heading_path: list[str],
        *,
        level: int | None = None,
        is_table: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> DocumentBlock:
        self._index += 1
        table_index: int | None = None
        if is_table:
            self.table_index += 1
            table_index = self.table_index
        block = DocumentBlock(
            document_id=self.document_id,
            file=self.file,
            kind=kind,
            text=text.strip("\n"),
            heading_path=list(heading_path),
            block_index=self._index,
            table_index=table_index,
            level=level,
            metadata=metadata or {},
        )
        self.blocks.append(block)
        return block
