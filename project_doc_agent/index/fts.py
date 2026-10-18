"""Полнотекстовый индекс SQLite FTS5."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from project_doc_agent.config import ProjectConfig
from project_doc_agent.errors import WorkspaceError
from project_doc_agent.ingest.parser import parse_corpus
from project_doc_agent.models import SourceDocument
from project_doc_agent.utils import normalize_ws, slugify, truncate
from project_doc_agent.validation.cards import split_front_matter
from project_doc_agent.workspace import Workspace

TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_]+")
H2_RE = re.compile(r"^##\s+(.+?)\s*$")
SNIPPET_LIMIT = 1200
MAX_MATCHES = 300


@dataclass
class SearchRecord:
    document_id: str
    file: str
    origin: str
    block_id: str
    kind: str
    heading_path: list[str]
    block_index: int
    table_index: int | None
    text: str


@dataclass
class SearchMatch:
    number: int
    file: str
    origin: str
    document_id: str
    block_id: str
    kind: str
    heading_path: list[str]
    snippet: str

    @property
    def section(self) -> str:
        return " / ".join(self.heading_path) if self.heading_path else "(без раздела)"


@dataclass
class SearchResult:
    query: str
    regex: bool
    context: int
    include_cards: bool
    records: int = 0
    matches: list[SearchMatch] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def files(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for match in self.matches:
            counts[match.file] = counts.get(match.file, 0) + 1
        return counts

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "regex": self.regex,
            "context": self.context,
            "include_cards": self.include_cards,
            "records": self.records,
            "total_matches": len(self.matches),
            "files": self.files,
            "warnings": self.warnings,
            "matches": [
                {
                    "number": match.number,
                    "file": match.file,
                    "origin": match.origin,
                    "document_id": match.document_id,
                    "block_id": match.block_id,
                    "kind": match.kind,
                    "heading_path": match.heading_path,
                    "section": match.section,
                    "snippet": match.snippet,
                }
                for match in self.matches
            ],
        }


def source_records(
    source_dir: Path,
    documents: list[SourceDocument],
    config: ProjectConfig,
) -> tuple[list[SearchRecord], list[str]]:
    corpus = parse_corpus(source_dir, documents, config)
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
    return records, corpus.warnings


def card_records(workspace: Workspace, config: ProjectConfig) -> list[SearchRecord]:
    records: list[SearchRecord] = []
    for path in workspace.card_files():
        if path.name == "index.md":
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        data, body, _ = split_front_matter(raw)
        card_id = str(data.get("id") or path.stem)
        title = str(data.get("title") or card_id)
        relative = workspace.relative(path)
        lines = body.splitlines()
        sections: list[tuple[str, list[str]]] = []
        current_title = ""
        current_content: list[str] = []
        for line in lines:
            match = H2_RE.match(line)
            if match:
                if current_title or current_content:
                    sections.append((current_title, current_content))
                current_title = match.group(1).strip()
                current_content = []
            else:
                current_content.append(line)
        if current_title or current_content:
            sections.append((current_title, current_content))
        for index, (section_title, content) in enumerate(sections, start=1):
            text = "\n".join(content).strip()
            if not text:
                continue
            heading_path = [title, section_title] if section_title else [title]
            records.append(
                SearchRecord(
                    document_id=card_id,
                    file=relative,
                    origin="card",
                    block_id=f"card:{card_id}:s-{index:03d}-{slugify(section_title, fallback='section', max_length=24)}",
                    kind="card_section",
                    heading_path=heading_path,
                    block_index=index,
                    table_index=None,
                    text=text,
                )
            )
    return records


class SearchIndex:
    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.db_path = workspace.index_dir / "fts.sqlite"

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def rebuild(self, records: list[SearchRecord]) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute("DROP TABLE IF EXISTS blocks_fts")
                connection.execute(
                    """
                    CREATE VIRTUAL TABLE blocks_fts USING fts5(
                        document_id, file, origin, block_id, kind, heading_path,
                        block_index UNINDEXED, table_index UNINDEXED, text,
                        tokenize='unicode61 remove_diacritics 2'
                    )
                    """
                )
                connection.executemany(
                    """
                    INSERT INTO blocks_fts
                    (document_id, file, origin, block_id, kind, heading_path, block_index, table_index, text)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            record.document_id,
                            record.file,
                            record.origin,
                            record.block_id,
                            record.kind,
                            " / ".join(record.heading_path),
                            record.block_index,
                            record.table_index,
                            record.text,
                        )
                        for record in records
                    ],
                )
        finally:
            connection.close()

    def _fts_block_ids(self, query: str) -> tuple[list[str], str | None]:
        tokens = TOKEN_RE.findall(query)
        if not tokens:
            return [], "В запросе нет слов для FTS5; использован substring-поиск"
        fts_query = " AND ".join(f'text : "{token}"' for token in tokens)
        connection = self._connect()
        try:
            cursor = connection.execute(
                "SELECT block_id FROM blocks_fts WHERE blocks_fts MATCH ? ORDER BY rowid", (fts_query,)
            )
            return [str(row[0]) for row in cursor.fetchall()], None
        except sqlite3.OperationalError as exc:
            return [], f"FTS5-запрос не выполнен ({exc}); использован substring-поиск"
        finally:
            connection.close()


    def search(
        self,
        records: list[SearchRecord],
        query: str,
        *,
        regex: bool = False,
        context: int = 1,
        include_cards: bool = False,
    ) -> SearchResult:
        if not query.strip():
            raise WorkspaceError("Пустой поисковый запрос")
        self.rebuild(records)
        result = SearchResult(
            query=query,
            regex=regex,
            context=max(0, context),
            include_cards=include_cards,
            records=len(records),
        )
        by_block = {record.block_id: record for record in records}
        by_document: dict[str, list[SearchRecord]] = {}
        for record in records:
            by_document.setdefault(record.document_id, []).append(record)
        for items in by_document.values():
            items.sort(key=lambda item: item.block_index)
        position = {
            (record.document_id, record.block_index): index
            for items in by_document.values()
            for index, record in enumerate(items)
        }

        if regex:
            try:
                pattern = re.compile(query, re.IGNORECASE | re.MULTILINE)
            except re.error as exc:
                raise WorkspaceError(f"Некорректное регулярное выражение: {exc}") from exc
            matched_records = [record for record in records if pattern.search(record.text)]
        else:
            block_ids, warning = self._fts_block_ids(query)
            if warning:
                result.warnings.append(warning)
            matched_records = [by_block[block_id] for block_id in block_ids if block_id in by_block]
            if not matched_records:
                needle = query.casefold()
                matched_records = [record for record in records if needle in record.text.casefold()]
                if matched_records:
                    result.warnings.append("FTS5 не дал результатов; использован substring-поиск")

        for record in matched_records[:MAX_MATCHES]:
            items = by_document.get(record.document_id, [record])
            index = position.get((record.document_id, record.block_index), 0)
            start = max(0, index - result.context)
            end = min(len(items), index + result.context + 1)
            snippet_text = "\n\n".join(item.text for item in items[start:end])
            result.matches.append(
                SearchMatch(
                    number=len(result.matches) + 1,
                    file=record.file,
                    origin=record.origin,
                    document_id=record.document_id,
                    block_id=record.block_id,
                    kind=record.kind,
                    heading_path=record.heading_path,
                    snippet=_make_snippet(snippet_text, query, regex),
                )
            )
        return result


def _make_snippet(text: str, query: str, regex: bool) -> str:
    text = normalize_ws(text)
    if len(text) <= SNIPPET_LIMIT:
        return text
    position = -1
    if regex:
        try:
            match = re.search(query, text, re.IGNORECASE)
            position = match.start() if match else -1
        except re.error:
            position = -1
    else:
        position = text.casefold().find(query.casefold())
    if position < 0:
        return truncate(text, SNIPPET_LIMIT)
    left = max(0, position - SNIPPET_LIMIT // 3)
    right = min(len(text), left + SNIPPET_LIMIT)
    prefix = "…" if left > 0 else ""
    suffix = "…" if right < len(text) else ""
    return f"{prefix}{text[left:right]}{suffix}"



def render_search_report(result: SearchResult) -> str:
    lines = [
        "# Отчёт поиска",
        "",
        f"Запрос: `{result.query}`",
        f"Режим: {'regex' if result.regex else 'FTS5/substring'}",
        f"Контекст: {result.context} блок(ов)",
        f"Проиндексировано блоков: {result.records}",
        f"Найдено совпадений: {len(result.matches)}",
        "",
    ]
    if result.warnings:
        lines.extend(["> " + warning for warning in result.warnings] + [""])
    if not result.matches:
        lines.extend(["Совпадений не найдено.", ""])
        return "\n".join(lines)
    grouped: dict[str, dict[str, list[SearchMatch]]] = {}
    for match in result.matches:
        grouped.setdefault(match.file, {}).setdefault(match.section, []).append(match)
    for file in sorted(grouped, key=str.casefold):
        lines.extend([f"## {file}", ""])
        for section in sorted(grouped[file], key=str.casefold):
            lines.extend([f"### {section}", ""])
            for match in grouped[file][section]:
                lines.append(f"- №{match.number}, `{match.kind}`, `{match.block_id}`: {match.snippet}")
            lines.append("")
    return "\n".join(lines)


def save_search_report(workspace: Workspace, run: Any, result: SearchResult) -> Path:
    run.write_json("matches.json", result.as_dict())
    report_path = workspace.atomic_write("reports/search_report.md", render_search_report(result))
    run.record_output(report_path)
    return report_path

