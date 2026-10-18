"""Чанкинг документов по иерархии заголовков."""

from __future__ import annotations

from dataclasses import dataclass, field

from project_doc_agent.models import Chunk, DocumentBlock, SourceRef


@dataclass
class _Segment:
    heading: DocumentBlock | None = None
    blocks: list[DocumentBlock] = field(default_factory=list)
    heading_path: list[str] = field(default_factory=list)

    @property
    def level(self) -> int:
        return self.heading.level or 0 if self.heading is not None else 0

    @property
    def content_blocks(self) -> list[DocumentBlock]:
        return [block for block in self.blocks if block.kind != "heading"]

    @property
    def chars(self) -> int:
        return sum(len(block.text) + 2 for block in self.content_blocks)


@dataclass
class _Accumulator:
    path: list[str]
    anchor: int
    parts: list[tuple[str, DocumentBlock]] = field(default_factory=list)


def _segments(blocks: list[DocumentBlock]) -> list[_Segment]:
    segments: list[_Segment] = []
    current = _Segment(heading=None, blocks=[], heading_path=[])
    for block in blocks:
        if block.kind == "heading":
            if current.blocks:
                segments.append(current)
            current = _Segment(heading=block, blocks=[block], heading_path=block.heading_path)
        else:
            if not current.blocks:
                current.heading_path = block.heading_path
            current.blocks.append(block)
    if current.blocks:
        segments.append(current)
    return segments


def _render_header(path: list[str]) -> str:
    if path:
        return "# " + " / ".join(path)
    return "# (без раздела)"


def _block_text(block: DocumentBlock) -> str:
    if block.kind == "code":
        language = str(block.metadata.get("language") or "")
        return f"```{language}\n{block.text}\n```"
    return block.text


def _split_text(text: str, limit: int) -> list[str]:
    if limit < 200:
        limit = 200
    if len(text) <= limit:
        return [text]
    pieces: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines():
        if current and current_len + len(line) + 1 > limit:
            pieces.append("\n".join(current))
            current = []
            current_len = 0
        while len(line) > limit:
            if current:
                pieces.append("\n".join(current))
                current = []
                current_len = 0
            pieces.append(line[:limit])
            line = line[limit:]
        current.append(line)
        current_len += len(line) + 1
    if current:
        pieces.append("\n".join(current))
    return [piece for piece in pieces if piece.strip()]


def _split_table(text: str, limit: int) -> list[str]:
    lines = text.splitlines()
    if len(lines) <= 3:
        return _split_text(text, limit)
    header = lines[:2]
    rows = lines[2:]
    header_text = "\n".join(header)
    if len(header_text) >= limit:
        return _split_text(text, limit)
    pieces: list[str] = []
    current = list(header)
    current_len = len(header_text) + 1
    for row in rows:
        if current_len + len(row) + 1 > limit and len(current) > len(header):
            pieces.append("\n".join(current))
            current = list(header)
            current_len = len(header_text) + 1
        current.append(row)
        current_len += len(row) + 1
    if len(current) > len(header):
        pieces.append("\n".join(current))
    return pieces or _split_text(text, limit)


def _parts_for_segment(segment: _Segment, max_chars: int, header_len: int) -> list[tuple[str, DocumentBlock]]:
    limit = max(400, max_chars - header_len)
    parts: list[tuple[str, DocumentBlock]] = []
    if segment.heading:
        level = segment.heading.level or 1
        parts.append((f"{'#' * min(level, 6)} {segment.heading.text}", segment.heading))
    for block in segment.content_blocks:
        text = _block_text(block)
        if len(text) <= limit:
            parts.append((text, block))
            continue
        pieces = _split_table(text, limit) if block.kind == "table" else _split_text(text, limit)
        parts.extend((piece, block) for piece in pieces)
    return parts


def _pack_parts(
    parts: list[tuple[str, DocumentBlock]],
    path: list[str],
    max_chars: int,
) -> list[list[tuple[str, DocumentBlock]]]:
    header_len = len(_render_header(path)) + 2
    limit = max(400, max_chars - header_len)
    packed: list[list[tuple[str, DocumentBlock]]] = []
    current: list[tuple[str, DocumentBlock]] = []
    current_len = 0
    for text, block in parts:
        pieces = _split_text(text, limit) if len(text) > limit else [text]
        for piece in pieces:
            if current and current_len + len(piece) + 2 > limit:
                packed.append(current)
                current = []
                current_len = 0
            current.append((piece, block))
            current_len += len(piece) + 2
            if current_len >= limit:
                packed.append(current)
                current = []
                current_len = 0
    if current:
        packed.append(current)
    return packed


def _make_chunk(
    document_id: str,
    file: str,
    path: list[str],
    parts: list[tuple[str, DocumentBlock]],
    index: int,
    document_sha: str,
) -> Chunk:
    header = _render_header(path)
    text = header + "\n\n" + "\n\n".join(part for part, _ in parts)
    block_ids: list[str] = []
    refs: list[SourceRef] = []
    seen: set[str] = set()
    table_count = 0
    for _, block in parts:
        if block.block_id in seen:
            continue
        seen.add(block.block_id)
        block_ids.append(block.block_id)
        refs.append(SourceRef(file=file, section=block.section, block_id=block.block_id, sha256=document_sha))
        if block.kind == "table":
            table_count += 1
    return Chunk(
        chunk_id=f"{document_id}:c-{index:04d}",
        document_id=document_id,
        file=file,
        chunk_index=index,
        heading_path=list(path),
        block_ids=block_ids,
        source_refs=refs,
        text=text,
        chars=len(text),
        table_count=table_count,
    )


def chunk_document(
    document_id: str,
    file: str,
    blocks: list[DocumentBlock],
    *,
    max_chars: int = 6000,
    document_sha: str = "",
) -> list[Chunk]:
    segments = [segment for segment in _segments(blocks) if segment.content_blocks]
    chunks: list[Chunk] = []
    current: _Accumulator | None = None

    def flush() -> None:
        nonlocal current
        if current is None or not current.parts:
            current = None
            return
        index = len(chunks) + 1
        chunks.append(_make_chunk(document_id, file, current.path, current.parts, index, document_sha))
        current = None

    for segment in segments:
        path = segment.heading_path or next(
            (block.heading_path for block in segment.content_blocks), []
        )
        header_len = len(_render_header(path)) + 2

        if current is not None and (current.anchor == 0 or segment.level <= current.anchor):
            flush()
        if current is not None and current.anchor > 0 and segment.level > current.anchor:
            estimated = sum(len(text) + 2 for text, _ in current.parts) + segment.chars + header_len
            if estimated > max_chars:
                flush()

        parts = _parts_for_segment(segment, max_chars, header_len)
        segment_chars = sum(len(text) + 2 for text, _ in parts)
        if current is None:
            current = _Accumulator(path=path, anchor=segment.level or 0)

        if segment_chars + header_len > max_chars:
            if current.parts:
                flush()
            for packed in _pack_parts(parts, path, max_chars):
                index = len(chunks) + 1
                chunks.append(_make_chunk(document_id, file, path, packed, index, document_sha))
            current = None
            continue

        current.parts.extend(parts)
    flush()
    return chunks

