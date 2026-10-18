"""Тесты чанкинга."""

from __future__ import annotations

from project_doc_agent.config import make_project_config
from project_doc_agent.ingest.chunking import chunk_document
from project_doc_agent.ingest.inventory import build_inventory
from project_doc_agent.ingest.parser import parse_corpus
from project_doc_agent.models import DocumentBlock


def _config(source):
    return make_project_config(
        client="marta",
        client_name="ООО",
        project="toplog",
        project_name="TopLog",
        bases=["ut11"],
        source_dir=str(source),
    )


def test_chunks_have_refs_and_heading_context(sample_source) -> None:
    config = _config(sample_source)
    inventory = build_inventory(sample_source)
    corpus = parse_corpus(sample_source, inventory.documents, config)
    document = corpus.documents[0]
    blocks = [block for block in corpus.blocks if block.document_id == document.document_id]
    chunks = chunk_document(
        document.document_id,
        document.file,
        blocks,
        max_chars=450,
        document_sha=document.sha256,
    )
    assert chunks
    for chunk in chunks:
        assert chunk.source_refs
        assert chunk.block_ids
        assert chunk.chars <= 450
        assert chunk.text.startswith("# ")
        assert all(ref.block_id in {block.block_id for block in blocks} for ref in chunk.source_refs)


def test_large_table_is_split_with_header() -> None:
    rows = "\n".join(f"| row-{index} | value-{index} |" for index in range(200))
    block = DocumentBlock(
        document_id="doc-001",
        file="big.md",
        kind="table",
        text="| A | B |\n| --- | --- |\n" + rows,
        heading_path=["Таблица"],
        block_index=1,
        table_index=1,
    )
    chunks = chunk_document("doc-001", "big.md", [block], max_chars=1200)
    assert len(chunks) > 1
    for chunk in chunks:
        assert "| A | B |" in chunk.text
        assert chunk.block_ids == ["doc-001:b-001"]
