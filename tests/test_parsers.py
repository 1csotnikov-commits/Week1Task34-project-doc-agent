"""Тесты инвентаризации и парсеров."""

from __future__ import annotations

from pathlib import Path

from project_doc_agent.config import make_project_config
from project_doc_agent.ingest.inventory import build_inventory
from project_doc_agent.ingest.markdown_parser import parse_markdown
from project_doc_agent.ingest.parser import parse_corpus
from project_doc_agent.ingest.text_parser import parse_txt
from project_doc_agent.utils import sha256_file


def _config(source: Path):
    return make_project_config(
        client="marta",
        client_name="ООО",
        project="toplog",
        project_name="TopLog",
        bases=["ut11"],
        source_dir=str(source),
    )


def test_inventory_sha_and_ids(sample_source: Path) -> None:
    inventory = build_inventory(sample_source)
    assert len(inventory.documents) == 3
    assert [document.document_id for document in inventory.documents] == [
        "doc-001",
        "doc-002",
        "doc-003",
    ]
    for document in inventory.documents:
        assert document.sha256 == sha256_file(sample_source / document.file)
        assert document.size > 0


def test_markdown_headings_tables_and_front_matter(sample_source: Path) -> None:
    parsed = parse_markdown(
        (sample_source / "alpha.md").read_text(encoding="utf-8"),
        "doc-001",
        "alpha.md",
    )
    assert parsed.title == "Интеграционный дизайн"
    assert parsed.metadata["author"] == "Тест"
    headings = [block for block in parsed.blocks if block.kind == "heading"]
    assert [heading.heading_path for heading in headings][:2] == [
        ["Интеграция TopLog"],
        ["Интеграция TopLog", "Передача заказа"],
    ]
    assert any(block.kind == "table" for block in parsed.blocks)
    paragraph = next(block for block in parsed.blocks if "createShipmentOrder" in block.text)
    assert paragraph.heading_path[-1] == "Передача заказа"
    assert paragraph.block_id == "doc-001:b-003"


def test_txt_paragraphs(sample_source: Path) -> None:
    parsed = parse_txt(
        (sample_source / "notes.txt").read_text(encoding="utf-8"),
        "doc-003",
        "notes.txt",
    )
    assert len(parsed.blocks) == 2
    assert all(block.kind == "paragraph" for block in parsed.blocks)
    assert parsed.blocks[0].block_id == "doc-003:b-001"


def test_docx_headings_table_image(docx_source: Path) -> None:
    config = _config(docx_source)
    inventory = build_inventory(docx_source)
    corpus = parse_corpus(docx_source, inventory.documents, config)
    assert len(corpus.documents) == 1
    document = corpus.documents[0]
    assert document.table_count == 1
    assert document.image_count >= 1
    headings = [block for block in corpus.blocks if block.kind == "heading"]
    assert ["Интеграция"] == headings[0].heading_path
    assert ["Интеграция", "Передача заказа"] == headings[1].heading_path
    table = next(block for block in corpus.blocks if block.kind == "table")
    assert table.text.startswith("| Поле | Тип |")
    assert table.table_index == 1
    paragraph = next(block for block in corpus.blocks if "createShipmentOrder" in block.text)
    assert paragraph.heading_path == ["Интеграция", "Передача заказа"]
    image_items = [item for item in corpus.manual_review if item.kind == "image"]
    assert image_items
    assert image_items[0].section == "Интеграция / Передача заказа"
