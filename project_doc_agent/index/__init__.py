"""Поисковый индекс."""

from __future__ import annotations

from project_doc_agent.index.fts import (
    SearchIndex,
    SearchMatch,
    SearchRecord,
    SearchResult,
    card_records,
    render_search_report,
    save_search_report,
    source_records,
)

__all__ = [
    "SearchIndex",
    "SearchMatch",
    "SearchRecord",
    "SearchResult",
    "card_records",
    "render_search_report",
    "save_search_report",
    "source_records",
]
