"""Рендеринг Markdown-карточек, umbrella и index."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, cast

from project_doc_agent.config import ProjectConfig
from project_doc_agent.extract.grouping import CardDraft
from project_doc_agent.models import (
    Card,
    CardType,
    Fact,
    FactKind,
    OpenQuestion,
    ReviewStatus,
    SourceRef,
)
from project_doc_agent.utils import dedupe_strings, normalize_ws, today_iso

NO_DATA = "Нет данных в источниках."


@dataclass
class CardMeta:
    id: str
    title: str
    topic: str
    files: set[str] = field(default_factory=set)
    systems: set[str] = field(default_factory=set)
    objects: set[str] = field(default_factory=set)


def _unique_refs(refs: list[SourceRef]) -> list[SourceRef]:
    seen: set[str] = set()
    result: list[SourceRef] = []
    for ref in refs:
        key = f"{ref.file}|{ref.section}"
        if key in seen:
            continue
        seen.add(key)
        result.append(ref)
    return result


def _sources_block(refs: list[SourceRef]) -> list[str]:
    lines: list[str] = []
    for ref in _unique_refs(refs):
        section = ref.section or "(без раздела)"
        lines.append(f"- {ref.file} — {section} (`{ref.block_id}`)")
    return lines or [NO_DATA]


def _facts_with_kinds(facts: list[Fact], kinds: set[FactKind]) -> list[Fact]:
    selected = [fact for fact in facts if fact.kind in kinds]
    return selected or facts


def _fact_bullets(facts: list[Fact], include_details: bool = True) -> list[str]:
    lines: list[str] = []
    for fact in facts:
        summary = normalize_ws(fact.summary)
        header = f"- **{fact.title}**" if not summary else f"- **{fact.title}** — {summary}"
        lines.append(header)
        if include_details:
            for detail in fact.details:
                lines.append(f"  - {detail}")
    return lines


def _details_matching(facts: list[Fact], keywords: tuple[str, ...], limit: int = 10) -> list[str]:
    matches: list[str] = []
    for fact in facts:
        for text in [fact.summary, *fact.details]:
            lowered = text.casefold()
            if text and any(keyword in lowered for keyword in keywords):
                matches.append(text)
    return dedupe_strings(matches)[:limit]


def _related_lines(meta: CardMeta, cards: list[CardMeta], project_id: str, project_name: str) -> list[str]:
    lines = [f"- [[{project_id}|{project_name}]] — umbrella проекта"]
    scored: list[tuple[int, str, str]] = []
    for other in cards:
        if other.id == meta.id:
            continue
        score = len(meta.files & other.files) * 2 + len(meta.systems & other.systems) + len(meta.objects & other.objects)
        if score:
            scored.append((score, other.title, other.id))
    for _, title, card_id in sorted(scored, key=lambda item: (-item[0], item[1].casefold()))[:6]:
        lines.append(f"- [[{card_id}|{title}]]")
    return lines


def _integration_sections(
    draft: CardDraft,
    meta: CardMeta,
    cards: list[CardMeta],
    project_id: str,
    project_name: str,
    questions: list[OpenQuestion],
) -> list[tuple[str, list[str]]]:
    facts = draft.facts
    systems = dedupe_strings([system for fact in facts for system in fact.systems])
    directions = dedupe_strings(
        [fact.direction.value for fact in facts if fact.direction.value not in {"none", "both"}]
    )
    methods = dedupe_strings([method for fact in facts for method in fact.methods])
    objects = dedupe_strings([obj for fact in facts for obj in fact.objects])
    summaries = dedupe_strings([fact.summary for fact in facts if fact.summary])[:4]

    purpose = [*summaries] if summaries else [NO_DATA]
    participants: list[str] = []
    if systems:
        participants.append("- Системы: " + ", ".join(systems))
    if directions:
        participants.append("- Направления обмена: " + ", ".join(directions))
    participants = participants or [NO_DATA]

    flows = [fact for fact in facts if fact.kind is FactKind.flow]
    if flows:
        scenarios = _fact_bullets(flows)
    else:
        scenario_details = _details_matching(facts, ("переда", "выгруж", "загруж", "созда", "обмен", "обработ"))
        scenarios = [f"- {detail}" for detail in scenario_details] or [NO_DATA]

    formats: list[str] = []
    if methods:
        formats.append("- Методы: " + ", ".join(f"`{method}`" for method in methods))
    format_details = _details_matching(
        facts, ("json", "xml", "base64", "rest", "soap", "http", "формат", "структура", "полей", "таблиц")
    )
    formats.extend(f"- {detail}" for detail in format_details)
    if formats:
        formats.append("- Таблицы полей и структуры — в источниках (см. раздел «Источники»).")
    formats = formats or [NO_DATA]

    data_items = list(objects)
    for fact in facts:
        if fact.kind is FactKind.integration_object:
            data_items.append(fact.title)
    data_items = dedupe_strings(data_items)
    data = [f"- {item}" for item in data_items] or [NO_DATA]

    errors = [fact for fact in facts if fact.kind is FactKind.error_handling]
    if errors:
        error_lines = _fact_bullets(errors)
    else:
        error_details = _details_matching(facts, ("ошиб", "мониторинг", "статус", "повтор", "контрол", "журнал"))
        error_lines = [f"- {detail}" for detail in error_details]
    error_lines = error_lines or [NO_DATA]

    check_lines: list[str] = []
    if methods:
        check_lines.extend(f"- Проверить вызов метода `{method}`." for method in methods)
    check_details = _details_matching(facts, ("провер", "тест", "запрос", "контрол", "вызов", "сценари"))
    check_lines.extend(f"- {detail}" for detail in check_details if detail not in check_lines)
    check_lines = check_lines or [NO_DATA]

    limits = [
        "- Карточка собрана автоматически по перечисленным источникам; исходные документы имеют приоритет.",
        "- Изображения и диаграммы в MVP не анализируются (см. reports/manual_review.md).",
    ]
    if any(fact.confidence.value == "low" for fact in facts):
        limits.append("- Часть утверждений имеет низкую уверенность и требует ручной проверки.")
    if draft.review_note:
        limits.append(f"- {draft.review_note}")

    question_lines: list[str] = []
    for question in questions:
        if meta.id in question.cards:
            question_lines.append(f"- {question.question}")
    for fact in facts:
        question_lines.extend(f"- {question}" for question in fact.open_questions)
    question_lines = dedupe_strings(question_lines) or ["Открытых вопросов по теме нет."]

    return [
        ("Назначение", purpose),
        ("Участники и системы", participants),
        ("Сценарии обмена", scenarios),
        ("Форматы и методы", formats),
        ("Данные и справочники", data),
        ("Ошибки и мониторинг", error_lines),
        ("Как проверить", check_lines),
        ("Ограничения и допущения", limits),
        ("Открытые вопросы", question_lines),
        ("Источники", _sources_block([ref for fact in facts for ref in fact.source_refs])),
        ("Связанные заметки", _related_lines(meta, cards, project_id, project_name)),
    ]


def _task_sections(
    draft: CardDraft,
    meta: CardMeta,
    cards: list[CardMeta],
    project_id: str,
    project_name: str,
    questions: list[OpenQuestion],
) -> list[tuple[str, list[str]]]:
    facts = draft.facts
    purpose = dedupe_strings([fact.summary for fact in facts if fact.summary])[:3] or [NO_DATA]
    details = dedupe_strings([detail for fact in facts for detail in fact.details])
    done = [f"- {detail}" for detail in details] or _fact_bullets(facts)
    methods = dedupe_strings([method for fact in facts for method in fact.methods])
    checks = [f"- Проверить вызов метода `{method}`." for method in methods]
    checks.extend(
        f"- {detail}"
        for detail in _details_matching(facts, ("провер", "тест", "запрос", "контрол", "вызов"))
        if f"- {detail}" not in checks
    )
    checks = checks or [NO_DATA]
    risks = ["- Карточка собрана автоматически; исходные документы имеют приоритет."]
    if draft.review_note:
        risks.append(f"- {draft.review_note}")
    for question in questions:
        if meta.id in question.cards:
            risks.append(f"- Открытый вопрос: {question.question}")
    for fact in facts:
        risks.extend(f"- Открытый вопрос: {question}" for question in fact.open_questions)
    return [
        ("Зачем", purpose),
        ("Что сделано", done),
        ("Как проверить", checks),
        ("Риски и ограничения", dedupe_strings(risks)),
        ("Связанные заметки", _related_lines(meta, cards, project_id, project_name)),
    ]



TYPE_TAGS = {
    CardType.integration: "интеграция",
    CardType.reference: "справочник",
    CardType.flow: "поток",
    CardType.requirement: "требование",
    CardType.error: "ошибки",
    CardType.auth: "авторизация",
    CardType.scope: "объём-работ",
    CardType.glossary: "глоссарий",
    CardType.other: "прочее",
    CardType.project: "проект",
}


def _body(title: str, sections: list[tuple[str, list[str]]]) -> str:
    lines = [f"# {title}", ""]
    for section_title, content in sections:
        lines.extend([f"## {section_title}", ""])
        lines.extend(content)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_card(
    draft: CardDraft,
    config: ProjectConfig,
    cards: list[CardMeta],
    questions: list[OpenQuestion],
    *,
    created: str | None = None,
    updated: str | None = None,
) -> Card:
    updated_date = updated or today_iso()
    meta = CardMeta(
        id=f"{config.project_id}-{draft.topic}",
        title=draft.title,
        topic=draft.topic,
        files={ref.file for fact in draft.facts for ref in fact.source_refs},
        systems={system for fact in draft.facts for system in fact.systems},
        objects={obj for fact in draft.facts for obj in fact.objects},
    )
    refs = _unique_refs([ref for fact in draft.facts for ref in fact.source_refs])
    if config.profile.value == "task":
        sections = _task_sections(draft, meta, cards, config.project_id, config.project_name, questions)
    else:
        sections = _integration_sections(draft, meta, cards, config.project_id, config.project_name, questions)
    body = _body(draft.title, sections)
    return Card(
        id=meta.id,
        client=config.client,
        client_name=config.client_name,
        project=config.project,
        project_name=config.project_name,
        bases=config.bases,
        title=draft.title,
        type=draft.card_type,
        profile=config.profile,
        status="draft",
        risk=cast(Literal["low", "medium", "high"], draft.risk if draft.risk in {"low", "medium", "high"} else "medium"),
        sources=refs,
        review_status=draft.review_status,
        review_note=draft.review_note,
        tags=[
            f"клиент/{config.client}",
            f"проект/{config.project}",
            TYPE_TAGS.get(draft.card_type, "прочее"),
        ],
        created=created or updated_date,
        updated=updated_date,
        parent=config.project_id,
        body=body,
    )


def _umbrella_refs(cards: list[Card]) -> list[SourceRef]:
    refs: list[SourceRef] = []
    seen: set[str] = set()
    for card in cards:
        for ref in card.sources:
            if ref.file in seen:
                continue
            seen.add(ref.file)
            refs.append(ref)
    return refs


def render_umbrella(
    config: ProjectConfig,
    cards: list[Card],
    questions: list[OpenQuestion],
    *,
    created: str | None = None,
    updated: str | None = None,
) -> Card:
    updated_date = updated or today_iso()
    refs = _umbrella_refs(cards)
    if not refs:
        raise ValueError("Нельзя создать umbrella без источников")
    source_lines = [
        f"- {ref.file}" + (f" — начиная с раздела {ref.section}" if ref.section else "")
        for ref in refs
    ]
    card_lines = [
        f"- [[{card.id}|{card.title}]] — {card.type.value}, проверка: {card.review_status.value}"
        for card in sorted(cards, key=lambda item: item.title.casefold())
    ] or [NO_DATA]
    question_lines = [f"- {question.question}" for question in questions[: config.max_questions]]
    question_lines = question_lines or ["Открытых вопросов по проекту нет."]
    dataview = "\n".join(
        [
            "```dataview",
            'TABLE title AS "Карточка", type AS "Тип", review_status AS "Проверка", updated AS "Обновлено"',
            f'FROM "{config.obsidian_folder}"',
            f'WHERE project = "{config.project}" AND type != "project"',
            "SORT title ASC",
            "```",
        ]
    )
    body = _body(
        config.project_name,
        [
            (
                "Назначение",
                [
                    f"Документация проекта «{config.project_name}» для клиента {config.client_name}.",
                    f"Состав: {len(cards)} тематических карточек и эта umbrella-карточка.",
                ],
            ),
            (
                "Клиент и базы",
                [
                    f"- Клиент: {config.client_name} (`{config.client}`)",
                    f"- Базы: {', '.join(f'`{base}`' for base in config.bases)}",
                    f"- Профиль: `{config.profile.value}`",
                ],
            ),
            ("Состав карточек", [*card_lines, "", dataview]),
            ("Открытые вопросы", question_lines),
            ("Источники", source_lines),
            ("Связанные заметки", ["- [[index|Индекс документации]]"]),
        ],
    )
    return Card(
        id=config.project_id,
        client=config.client,
        client_name=config.client_name,
        project=config.project,
        project_name=config.project_name,
        bases=config.bases,
        title=config.project_name,
        type=CardType.project,
        profile=config.profile,
        status="draft",
        risk="medium",
        sources=refs,
        review_status=(
            ReviewStatus.needs_review
            if any(card.review_status is not ReviewStatus.ok for card in cards)
            else ReviewStatus.ok
        ),
        review_note=(
            "Часть карточек проекта требует ручной проверки."
            if any(card.review_status is not ReviewStatus.ok for card in cards)
            else None
        ),
        tags=[f"клиент/{config.client}", f"проект/{config.project}", "проект"],
        created=created or updated_date,
        updated=updated_date,
        parent=None,
        body=body,
    )



def render_index(config: ProjectConfig, cards: list[Card], *, updated: str | None = None) -> str:
    updated_date = updated or today_iso()
    lines = [
        "# Индекс документации",
        "",
        f"Проект: [[{config.project_id}|{config.project_name}]]",
        "",
        f"Обновлено: {updated_date}",
        "",
        "| Карточка | Тип | Проверка | Обновлено |",
        "| --- | --- | --- | --- |",
    ]
    for card in sorted(cards, key=lambda item: item.title.casefold()):
        lines.append(
            f"| [[{card.id}\\|{card.title}]] | {card.type.value} | "
            f"{card.review_status.value} | {card.updated} |"
        )
    if not cards:
        lines.append(f"| {NO_DATA} | — | — | — |")
    lines.extend(
        [
            "",
            f"Umbrella: [[{config.project_id}|{config.project_name}]]",
            "",
            f"Папка Obsidian: `{config.obsidian_folder}`",
            "",
        ]
    )
    return "\n".join(lines)


@dataclass
class RenderedCards:
    cards: list[Card] = field(default_factory=list)
    umbrella: Card | None = None
    files: dict[str, str] = field(default_factory=dict)
    metas: list[CardMeta] = field(default_factory=list)


def render_all(
    drafts: list[CardDraft],
    config: ProjectConfig,
    questions: list[OpenQuestion],
    *,
    created_map: dict[str, str] | None = None,
    updated: str | None = None,
) -> RenderedCards:
    updated_date = updated or today_iso()
    created_map = created_map or {}
    metas: list[CardMeta] = []
    for draft in drafts:
        metas.append(
            CardMeta(
                id=f"{config.project_id}-{draft.topic}",
                title=draft.title,
                topic=draft.topic,
                files={ref.file for fact in draft.facts for ref in fact.source_refs},
                systems={system for fact in draft.facts for system in fact.systems},
                objects={obj for fact in draft.facts for obj in fact.objects},
            )
        )
    render_result = RenderedCards(metas=metas)
    for draft in sorted(drafts, key=lambda item: item.title.casefold()):
        card_id = f"{config.project_id}-{draft.topic}"
        card = render_card(
            draft,
            config,
            metas,
            questions,
            created=created_map.get(card_id),
            updated=updated_date,
        )
        render_result.cards.append(card)
        render_result.files[f"{config.output_dir}/{card.id}.md"] = card.to_markdown()
    if render_result.cards:
        umbrella = render_umbrella(
            config,
            render_result.cards,
            questions,
            created=created_map.get(config.project_id),
            updated=updated_date,
        )
        render_result.umbrella = umbrella
        render_result.files[f"{config.output_dir}/{config.project_id}.md"] = umbrella.to_markdown()
    render_result.files[f"{config.output_dir}/index.md"] = render_index(
        config, render_result.cards, updated=updated_date
    )
    return render_result

