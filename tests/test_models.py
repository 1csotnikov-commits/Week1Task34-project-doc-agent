"""Тесты моделей Pydantic."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from project_doc_agent.models import Card, Confidence, Fact, FactKind, SourceRef


def test_fact_requires_source_ref() -> None:
    with pytest.raises(ValidationError):
        Fact(fact_id="f-1", title="Факт", summary="Описание", source_refs=[])


def test_fact_normalizes_topic_and_refs() -> None:
    fact = Fact(
        fact_id="f-1",
        card_topic="Передача Номенклатуры",
        kind=FactKind.integration_object,
        title="  Передача   справочника ",
        summary=" Описание ",
        source_refs=[SourceRef(file="a.md", section="1", block_id="doc-001:b-001")],
        confidence=Confidence.high,
    )
    assert fact.card_topic == "peredacha-nomenklatury"
    assert fact.title == "Передача справочника"


def test_card_rejects_wrong_id_prefix() -> None:
    with pytest.raises(ValidationError):
        Card(
            id="other-project-topic",
            client="marta",
            client_name="ООО",
            project="toplog",
            project_name="TopLog",
            bases=["ut11"],
            title="Карточка",
            sources=[SourceRef(file="a.md", block_id="doc-001:b-001")],
        )
