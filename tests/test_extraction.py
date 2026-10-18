"""Тесты извлечения фактов и группировки."""

from __future__ import annotations

from project_doc_agent.config import ProjectConfig
from project_doc_agent.extract.facts import extract_all
from project_doc_agent.extract.grouping import group_facts
from project_doc_agent.ingest.chunking import chunk_document
from project_doc_agent.ingest.inventory import build_inventory
from project_doc_agent.ingest.parser import parse_corpus
from project_doc_agent.models import ReviewStatus
from project_doc_agent.runs.manifest import RunContext
from project_doc_agent.workspace import Workspace
from tests.fakes import FakeProvider


def test_extract_facts_and_conflicts(workspace: tuple[Workspace, ProjectConfig]) -> None:
    ws, config = workspace
    source = config.source_path(ws.root)
    inventory = build_inventory(source)
    corpus = parse_corpus(source, inventory.documents, config)
    chunks = []
    for document in corpus.documents:
        blocks = [block for block in corpus.blocks if block.document_id == document.document_id]
        chunks.extend(
            chunk_document(
                document.document_id,
                document.file,
                blocks,
                max_chars=config.max_chunk_chars,
                document_sha=document.sha256,
            )
        )
    run = RunContext.create(ws, "test-extract", prompt_names=("extract.md",))
    provider = FakeProvider()
    extraction = extract_all(
        chunks=chunks,
        blocks=corpus.blocks,
        provider=provider,
        run=run,
        config=config,
    )
    assert extraction.facts
    assert provider.calls == len(chunks)
    allowed = set(corpus.block_map)
    for fact in extraction.facts:
        assert fact.source_refs
        assert all(ref.block_id in allowed for ref in fact.source_refs)
        assert fact.fact_id.startswith("doc-")

    grouped = group_facts(extraction.facts, config)
    topics = {card.topic: card for card in grouped.cards}
    assert "documents" in topics
    assert topics["documents"].review_status is ReviewStatus.needs_review
    assert grouped.conflicts
    assert any("направлен" in conflict.description for conflict in grouped.conflicts)
    assert any(question.question for question in grouped.questions)
    assert run.manifest.llm_calls
