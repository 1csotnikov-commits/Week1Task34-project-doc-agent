"""Человекочитаемые отчёты."""

from __future__ import annotations

from project_doc_agent.config import ProjectConfig
from project_doc_agent.models import Card, Conflict, ManualReviewItem, OpenQuestion, SourceDocument
from project_doc_agent.utils import dedupe_strings, today_iso


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def render_sources(
    cards: list[Card],
    documents: list[SourceDocument],
    *,
    updated: str | None = None,
) -> str:
    updated_date = updated or today_iso()
    lines = [
        "# Источники",
        "",
        f"Обновлено: {updated_date}",
        "",
        "| Источник | Раздел | Карточка | block_id |",
        "| --- | --- | --- | --- |",
    ]
    rows: list[tuple[str, str, str, str]] = []
    for card in cards:
        for ref in card.sources:
            rows.append((ref.file, ref.section or "(без раздела)", card.title, ref.block_id))
    for row in sorted(rows, key=lambda item: (item[0].casefold(), item[1].casefold(), item[2].casefold())):
        lines.append("| " + " | ".join(_escape(value) for value in row) + " |")
    if not rows:
        lines.append("| Нет данных | — | — | — |")
    lines.extend(["", "## Документы", ""])
    for document in documents:
        status = "проанализирован" if document.block_count else "не разобран"
        lines.append(
            f"- `{document.file}` — {status}; блоков: {document.block_count}, "
            f"таблиц: {document.table_count}, изображений: {document.image_count}; "
            f"SHA-256: `{document.sha256[:12]}…`"
        )
        for warning in document.warnings:
            lines.append(f"  - Предупреждение: {warning}")
    lines.append("")
    return "\n".join(lines)


def render_open_questions(
    questions: list[OpenQuestion],
    config: ProjectConfig,
    *,
    updated: str | None = None,
) -> str:
    updated_date = updated or today_iso()
    lines = [
        "# Открытые вопросы",
        "",
        f"Обновлено: {updated_date}",
        "",
        "Вопросы собраны автоматически. По умолчанию агент не задаёт их в CLI; "
        "используйте `--interactive`, чтобы ответить на вопросы при запуске.",
        "",
    ]
    if not questions:
        lines.extend(["Открытых вопросов нет.", ""])
        return "\n".join(lines)
    grouped: dict[str, list[OpenQuestion]] = {}
    unassigned: list[OpenQuestion] = []
    for question in questions[: config.max_questions]:
        if question.cards:
            for card_id in question.cards:
                grouped.setdefault(card_id, []).append(question)
        else:
            unassigned.append(question)
    for card_id in sorted(grouped, key=str.casefold):
        lines.extend([f"## {card_id}", ""])
        for question in grouped[card_id]:
            marker = "x" if question.status == "answered" else " "
            lines.append(f"- [{marker}] {question.question}")
            if question.why:
                lines.append(f"  - Зачем: {question.why}")
            for ref in question.sources:
                lines.append(f"  - Источник: `{ref.file}` — {ref.section or '(без раздела)'} (`{ref.block_id}`)")
            if question.answer:
                lines.append(f"  - Ответ: {question.answer}")
        lines.append("")
    if unassigned:
        lines.extend(["## Общие вопросы", ""])
        for question in unassigned:
            lines.append(f"- [ ] {question.question}")
            if question.why:
                lines.append(f"  - Зачем: {question.why}")
        lines.append("")
    return "\n".join(lines)


def render_conflicts(conflicts: list[Conflict], *, updated: str | None = None) -> str:
    updated_date = updated or today_iso()
    lines = ["# Противоречия", "", f"Обновлено: {updated_date}", ""]
    if not conflicts:
        lines.extend(["Противоречий между источниками не обнаружено.", ""])
        return "\n".join(lines)
    for index, conflict in enumerate(conflicts, start=1):
        lines.extend([f"## {index}. {conflict.title}", "", conflict.description, ""])
        for statement in conflict.statements:
            lines.append(f"- {statement}")
        lines.append("")
        for ref in conflict.source_refs:
            lines.append(f"- Источник: `{ref.file}` — {ref.section or '(без раздела)'} (`{ref.block_id}`)")
        lines.append("")
    return "\n".join(lines)


def render_manual_review(
    items: list[ManualReviewItem],
    warnings: list[str] | None = None,
    *,
    updated: str | None = None,
) -> str:
    updated_date = updated or today_iso()
    lines = [
        "# Ручная проверка",
        "",
        f"Обновлено: {updated_date}",
        "",
        "В MVP не анализируются изображения, диаграммы, сканы и OLE-объекты. "
        "Ниже перечислены фрагменты, которые стоит проверить вручную.",
        "",
    ]
    if items:
        lines.extend(
            [
                "| Файл | Раздел | Причина | Тип | block_id |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for item in items:
            lines.append(
                "| "
                + " | ".join(
                    _escape(value)
                    for value in (
                        item.file,
                        item.section or "(без раздела)",
                        item.reason,
                        item.kind,
                        item.block_id or "",
                    )
                )
                + " |"
            )
    else:
        lines.append("Фрагментов, требующих ручной проверки, не найдено.")
    unique_warnings = dedupe_strings(warnings or [])
    if unique_warnings:
        lines.extend(["", "## Предупреждения пайплайна", ""])
        lines.extend(f"- {warning}" for warning in unique_warnings)
    lines.append("")
    return "\n".join(lines)
