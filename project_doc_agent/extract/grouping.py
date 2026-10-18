"""Группировка фактов в карточки и поиск противоречий."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from pydantic import BaseModel, ConfigDict

from project_doc_agent.config import ProjectConfig
from project_doc_agent.models import (
    CardType,
    Confidence,
    Conflict,
    Direction,
    Fact,
    FactKind,
    OpenQuestion,
    ReviewStatus,
    SourceRef,
)
from project_doc_agent.utils import normalize_title_key, normalize_ws

KIND_TO_TYPE: dict[FactKind, CardType] = {
    FactKind.integration_object: CardType.reference,
    FactKind.flow: CardType.flow,
    FactKind.requirement: CardType.requirement,
    FactKind.error_handling: CardType.error,
    FactKind.auth: CardType.auth,
    FactKind.scope: CardType.scope,
    FactKind.glossary: CardType.glossary,
    FactKind.other: CardType.other,
}

CANONICAL_TITLES: dict[str, str] = {
    "overview": "Обзор интеграции",
    "transport": "Транспортный пакет и протоколы обмена",
    "reference-data": "Справочники и НСИ",
    "reference-nomenclature": "Справочники: номенклатура и контрагенты",
    "reference-warehouse": "Справочники: склад и упаковка",
    "documents": "Документы и сценарии обмена",
    "documents-receipt": "Документы поступления и приёмки",
    "documents-shipment": "Документы отгрузки и сборки",
    "documents-inventory": "Инвентаризация, корректировки и отмены",
    "errors-monitoring": "Ошибки и мониторинг",
    "authorization": "Авторизация и настройки доступа",
    "scope": "Объём работ и ответственность",
    "glossary": "Глоссарий и сокращения",
}

CANONICAL_TYPES: dict[str, CardType] = {
    "overview": CardType.integration,
    "transport": CardType.integration,
    "reference-data": CardType.reference,
    "reference-nomenclature": CardType.reference,
    "reference-warehouse": CardType.reference,
    "documents": CardType.flow,
    "documents-receipt": CardType.flow,
    "documents-shipment": CardType.flow,
    "documents-inventory": CardType.flow,
    "errors-monitoring": CardType.error,
    "authorization": CardType.auth,
    "scope": CardType.scope,
    "glossary": CardType.glossary,
}

TRANSPORT_MARKERS = (
    "транспортный пакет",
    "транспортное сообщение",
    "протокол",
    "rest",
    "soap",
    "json",
    "xml",
    "base64",
    "http",
    "message",
    "сообщени",
    "веб-сервис",
    "webservice",
    "endpoint",
    "точка подключени",
)
REFERENCE_MARKERS = (
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
    "транспортн",
    "производител",
    "собственник",
    "классификатор",
    "регистр соответств",
    "нси",
)
DOCUMENT_MARKERS = (
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
    "перемещен",
    "возврат",
    "списани",
)
ERROR_MARKERS = ("ошиб", "мониторинг", "исключительн", "контроль", "статус", "повторн", "журнал")
AUTH_MARKERS = ("авторизац", "пользовател", "настройк", "полномоч", "доступ", "логин", "пароль")
SCOPE_MARKERS = (
    "объем работ",
    "объём работ",
    "ответственност",
    "срок",
    "стоимост",
    "этап",
    "команда",
    "гарантия",
    "зон",
)
GLOSSARY_MARKERS = ("глоссари", "термин", "сокращен", "сокращён")


def _canonical_topic(fact: Fact) -> str:
    topic = fact.card_topic.casefold()
    title_area = " ".join([topic, fact.title, *fact.objects, *fact.methods]).casefold()
    full_area = " ".join([title_area, fact.summary, *fact.details]).casefold()

    def has(markers: tuple[str, ...], area: str = title_area) -> bool:
        return any(marker in area for marker in markers)

    if fact.kind is FactKind.glossary or has(GLOSSARY_MARKERS, full_area):
        return "glossary"
    if fact.kind is FactKind.error_handling or has(ERROR_MARKERS, title_area):
        return "errors-monitoring"
    if fact.kind is FactKind.auth or has(AUTH_MARKERS, title_area):
        return "authorization"
    if fact.kind is FactKind.scope or has(SCOPE_MARKERS, title_area):
        return "scope"
    if has(TRANSPORT_MARKERS, title_area):
        return "transport"
    if has(REFERENCE_MARKERS, title_area):
        return "reference-data"
    if has(DOCUMENT_MARKERS, title_area):
        return "documents"
    if "обзор" in title_area or "архитектур" in title_area or "проект" in title_area:
        return "overview"
    if fact.kind is FactKind.integration_object:
        return "reference-data"
    if fact.kind is FactKind.flow:
        return "documents"
    if fact.kind is FactKind.requirement:
        return "scope"
    return "overview"


def _refine_topic(topic: str, fact: Fact) -> str:
    area = " ".join([fact.card_topic, fact.title, fact.summary, *fact.objects]).casefold()
    if topic == "documents":
        if any(marker in area for marker in ("поступлен", "приемк", "приёмк", "acceptance", "receiving")):
            return "documents-receipt"
        if any(marker in area for marker in ("отгруз", "сборк", "рейс", "shipment", "trip", "picking")):
            return "documents-shipment"
        if any(marker in area for marker in ("инвентаризац", "корректиров", "отмен", "inventory")):
            return "documents-inventory"
        return "documents"
    if topic == "reference-data":
        if any(marker in area for marker in ("номенклатур", "контрагент", "договор", "производител", "собственник")):
            return "reference-nomenclature"
        if any(marker in area for marker in ("склад", "вгх", "упаковк", "штрихкод", "водител", "температур", "транспортн")):
            return "reference-warehouse"
        return "reference-data"
    return topic


def _canonicalize(facts: list[Fact]) -> list[Fact]:
    base = [(fact, _canonical_topic(fact)) for fact in facts]
    counts = Counter(topic for _, topic in base)
    canonical: list[Fact] = []
    for fact, topic in base:
        if counts[topic] > 30:
            topic = _refine_topic(topic, fact)
        canonical.append(fact.model_copy(update={"card_topic": topic}))
    return canonical


class CardDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    topic: str
    title: str
    facts: list[Fact] = field(default_factory=list)
    review_status: ReviewStatus = ReviewStatus.ok
    review_note: str | None = None
    card_type: CardType = CardType.integration
    risk: str = "medium"


@dataclass
class GroupedResult:
    cards: list[CardDraft] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    questions: list[OpenQuestion] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize_title_key(left), normalize_title_key(right)).ratio()


def _conflict_between(first: Fact, second: Fact) -> str | None:
    if first.card_topic != second.card_topic:
        return None
    if {ref.file for ref in first.source_refs} & {ref.file for ref in second.source_refs}:
        return None
    similarity = _similarity(first.title, second.title)
    if similarity < 0.9:
        return None
    directions = {first.direction, second.direction}
    if Direction.none not in directions and Direction.both not in directions and len(directions) > 1:
        return "Источники указывают разные направления обмена"
    methods_first = {item.casefold() for item in first.methods}
    methods_second = {item.casefold() for item in second.methods}
    if methods_first and methods_second and not (methods_first & methods_second):
        return "Источники указывают разные методы для одной сущности"
    return None


def find_conflicts(facts: list[Fact]) -> list[Conflict]:
    conflicts: list[Conflict] = []
    seen: set[tuple[str, str]] = set()
    for index, first in enumerate(facts):
        for second in facts[index + 1 :]:
            reason = _conflict_between(first, second)
            if reason is None:
                continue
            pair = (min(first.fact_id, second.fact_id), max(first.fact_id, second.fact_id))
            if pair in seen:
                continue
            seen.add(pair)
            refs: list[SourceRef] = []
            for fact in (first, second):
                for ref in fact.source_refs:
                    if all(existing.block_id != ref.block_id for existing in refs):
                        refs.append(ref)
            conflicts.append(
                Conflict(
                    topic=first.card_topic,
                    title=first.title,
                    description=reason,
                    statements=[first.summary or first.title, second.summary or second.title],
                    source_refs=refs,
                )
            )
    return conflicts


def _merge_facts(facts: list[Fact]) -> list[Fact]:
    grouped: dict[tuple[str, str, str], Fact] = {}
    order: list[tuple[str, str, str]] = []
    for fact in facts:
        key = (fact.card_topic, fact.kind.value, normalize_title_key(fact.title))
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = fact.model_copy(deep=True)
            order.append(key)
            continue
        merged = grouped[key]
        merged.summary = max([merged.summary, fact.summary], key=len)
        merged.details = _unique([*merged.details, *fact.details])
        merged.systems = _unique([*merged.systems, *fact.systems])
        merged.methods = _unique([*merged.methods, *fact.methods])
        merged.objects = _unique([*merged.objects, *fact.objects])
        merged.open_questions = _unique([*merged.open_questions, *fact.open_questions])
        merged.source_refs = _unique_refs([*merged.source_refs, *fact.source_refs])
        confidence_order = {Confidence.low: 0, Confidence.medium: 1, Confidence.high: 2}
        if confidence_order[fact.confidence] > confidence_order[merged.confidence]:
            merged.confidence = fact.confidence
        if merged.direction is Direction.none:
            merged.direction = fact.direction
    return [grouped[key] for key in order]


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = normalize_ws(item).casefold()
        if key and key not in seen:
            seen.add(key)
            result.append(normalize_ws(item))
    return result


def _unique_refs(items: list[SourceRef]) -> list[SourceRef]:
    seen: set[str] = set()
    result: list[SourceRef] = []
    for item in items:
        if item.block_id in seen:
            continue
        seen.add(item.block_id)
        result.append(item)
    return result


def _card_type(facts: list[Fact]) -> CardType:
    counts = Counter(fact.kind for fact in facts)
    priority = [
        FactKind.integration_object,
        FactKind.flow,
        FactKind.requirement,
        FactKind.auth,
        FactKind.error_handling,
        FactKind.scope,
        FactKind.glossary,
        FactKind.other,
    ]
    best = max(priority, key=lambda kind: (counts.get(kind, 0), -priority.index(kind)))
    return KIND_TO_TYPE.get(best, CardType.integration)


def _choose_title(facts: list[Fact], topic: str) -> str:
    titles = Counter(normalize_ws(fact.title).rstrip(".") for fact in facts if fact.title)
    if not titles:
        return topic.replace("-", " ").capitalize()
    return max(titles, key=lambda title: (titles[title], len(title)))


def _questions_for_facts(facts: list[Fact], card_id: str) -> list[OpenQuestion]:
    questions: list[OpenQuestion] = []
    for fact in facts:
        for question in fact.open_questions:
            cleaned = normalize_ws(question)
            if not cleaned:
                continue
            questions.append(
                OpenQuestion(
                    question=cleaned,
                    why=f"Уточнение по факту «{fact.title}»",
                    cards=[card_id],
                    sources=fact.source_refs[:3],
                )
            )
    return questions


def group_facts(facts: list[Fact], config: ProjectConfig) -> GroupedResult:
    result = GroupedResult()
    if not facts:
        result.warnings.append("LLM не вернула ни одного факта")
        return result
    canonical_facts = _canonicalize(facts)
    raw_conflicts = find_conflicts(facts)
    canonical_by_title = {normalize_title_key(fact.title): fact.card_topic for fact in canonical_facts}
    conflicts = [
        conflict.model_copy(
            update={"topic": canonical_by_title.get(normalize_title_key(conflict.title), conflict.topic)}
        )
        for conflict in raw_conflicts
    ]
    result.conflicts = conflicts
    conflict_topics = {conflict.topic for conflict in conflicts}
    by_topic: dict[str, list[Fact]] = defaultdict(list)
    for fact in _merge_facts(canonical_facts):
        by_topic[fact.card_topic].append(fact)

    for topic in sorted(by_topic, key=str.casefold):
        topic_facts = by_topic[topic]
        title = CANONICAL_TITLES.get(topic) or _choose_title(topic_facts, topic)
        card_id = f"{config.project_id}-{topic}"
        status = ReviewStatus.ok
        note: str | None = None
        if topic in conflict_topics:
            status = ReviewStatus.needs_review
            note = "Обнаружены противоречия между источниками: см. reports/conflicts.md."
        elif all(fact.confidence is Confidence.low for fact in topic_facts):
            status = ReviewStatus.insufficient_data
            note = "Все факты по теме имеют низкую уверенность; данных недостаточно."
        elif any(fact.confidence is Confidence.low for fact in topic_facts):
            status = ReviewStatus.needs_review
            note = "Часть фактов имеет низкую уверенность и требует проверки."
        elif not any(fact.details or fact.summary for fact in topic_facts):
            status = ReviewStatus.insufficient_data
            note = "В источниках нет содержательных деталей по теме."
        result.cards.append(
            CardDraft(
                topic=topic,
                title=title,
                facts=topic_facts,
                review_status=status,
                review_note=note,
                card_type=CANONICAL_TYPES.get(topic, _card_type(topic_facts)),
                risk="high" if status is ReviewStatus.needs_review else "medium",
            )
        )
        result.questions.extend(_questions_for_facts(topic_facts, card_id))

    for conflict in conflicts:
        card_id = f"{config.project_id}-{conflict.topic}"
        result.questions.append(
            OpenQuestion(
                question=(
                    f"Какой вариант верен по теме «{conflict.title}»? "
                    f"{conflict.description}."
                ),
                why="Противоречие между источниками",
                cards=[card_id],
                sources=conflict.source_refs[:4],
            )
        )
    result.questions = _dedupe_questions(result.questions)
    result.warnings.append(f"Карточек сформировано: {len(result.cards)}; противоречий: {len(conflicts)}")
    return result


def _dedupe_questions(questions: list[OpenQuestion]) -> list[OpenQuestion]:
    grouped: dict[str, OpenQuestion] = {}
    order: list[str] = []
    for question in questions:
        key = normalize_ws(question.question).casefold()
        if key not in grouped:
            grouped[key] = question.model_copy(deep=True)
            order.append(key)
            continue
        existing = grouped[key]
        existing.cards = _unique([*existing.cards, *question.cards])
        existing.sources = _unique_refs([*existing.sources, *question.sources])
    return [grouped[key] for key in order]

