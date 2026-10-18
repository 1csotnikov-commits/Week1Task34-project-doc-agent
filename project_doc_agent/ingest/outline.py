"""Оглавление корпуса и потенциальные логические единицы."""

from __future__ import annotations

from typing import Any

from project_doc_agent.models import DocumentBlock, SourceDocument

LOGICAL_UNITS: dict[str, tuple[str, ...]] = {
    "transport": ("транспорт", "протокол", "rest", "soap", "json", "xml", "base64", "http", "message"),
    "reference_data": (
        "справочник",
        "номенклатур",
        "контрагент",
        "договор",
        "склад",
        "штрихкод",
        "вгх",
        "упаковк",
        "температур",
        "водител",
    ),
    "documents": (
        "документ",
        "поступлен",
        "отгруз",
        "заказ",
        "рейс",
        "приемк",
        "приёмк",
        "сборк",
        "инвентаризац",
        "корректиров",
        "комплектован",
    ),
    "errors": ("ошиб", "мониторинг", "исключен", "контроль", "статус", "повторн"),
    "auth": ("авторизац", "пользовател", "настройк", "полномоч", "доступ", "логин", "пароль"),
    "scope": ("объем работ", "объём работ", "ответственност", "срок", "стоимост", "этап", "договор"),
    "glossary": ("глоссари", "термин", "сокращен", "сокращён"),
}


def _unit_matches(text: str) -> set[str]:
    lowered = text.casefold()
    return {
        unit
        for unit, keywords in LOGICAL_UNITS.items()
        if any(keyword in lowered for keyword in keywords)
    }


def build_outline(documents: list[SourceDocument], blocks: list[DocumentBlock]) -> dict[str, Any]:
    by_document: dict[str, list[DocumentBlock]] = {}
    for block in blocks:
        by_document.setdefault(block.document_id, []).append(block)

    document_entries: list[dict[str, Any]] = []
    units: dict[str, set[str]] = {unit: set() for unit in LOGICAL_UNITS}
    for document in documents:
        document_blocks = by_document.get(document.document_id, [])
        headings = [
            {
                "block_id": block.block_id,
                "level": block.level,
                "title": block.text,
                "heading_path": block.heading_path,
            }
            for block in document_blocks
            if block.kind == "heading"
        ]
        tables = [
            {
                "block_id": block.block_id,
                "table_index": block.table_index,
                "heading_path": block.heading_path,
                "rows": block.metadata.get("rows"),
                "columns": block.metadata.get("columns"),
            }
            for block in document_blocks
            if block.kind == "table"
        ]
        for block in document_blocks:
            if block.kind in {"heading", "table"}:
                haystack = " ".join(block.heading_path + [block.text[:500]])
                for unit in _unit_matches(haystack):
                    units[unit].add(block.section)
        document_entries.append(
            {
                "document_id": document.document_id,
                "file": document.file,
                "title": document.title,
                "sha256": document.sha256,
                "size": document.size,
                "block_count": document.block_count,
                "table_count": document.table_count,
                "image_count": document.image_count,
                "headings": headings,
                "tables": tables,
                "warnings": document.warnings,
            }
        )

    logical_units = [
        {
            "unit": unit,
            "sections": sorted(sections, key=str.casefold)[:50],
            "section_count": len(sections),
        }
        for unit, sections in units.items()
        if sections
    ]
    logical_units.sort(key=lambda item: str(item["unit"]))
    return {
        "documents": document_entries,
        "logical_units": logical_units,
        "totals": {
            "documents": len(documents),
            "blocks": len(blocks),
            "tables": sum(document.table_count for document in documents),
            "images": sum(document.image_count for document in documents),
            "headings": sum(len(entry["headings"]) for entry in document_entries),
        },
    }
