"""Пайплайн ingest."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from project_doc_agent.config import ProjectConfig
from project_doc_agent.errors import IngestError, ProviderError
from project_doc_agent.extract.facts import extract_all
from project_doc_agent.extract.grouping import GroupedResult, group_facts
from project_doc_agent.ingest.chunking import chunk_document
from project_doc_agent.ingest.inventory import build_inventory
from project_doc_agent.ingest.outline import build_outline
from project_doc_agent.ingest.parser import parse_corpus
from project_doc_agent.models import ManualReviewItem, OpenQuestion, RunStatus
from project_doc_agent.providers.base import LLMProvider
from project_doc_agent.render.cards import RenderedCards, render_all
from project_doc_agent.render.reports import (
    render_conflicts,
    render_manual_review,
    render_open_questions,
    render_sources,
)
from project_doc_agent.runs.manifest import RunContext
from project_doc_agent.validation.cards import split_front_matter
from project_doc_agent.validation.diff import make_multi_diff
from project_doc_agent.validation.validator import validate_workspace
from project_doc_agent.workspace import Workspace


@dataclass
class IngestOutcome:
    run_id: str
    card_count: int = 0
    fact_count: int = 0
    chunk_count: int = 0
    conflict_count: int = 0
    question_count: int = 0
    files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    dry_run: bool = False


def _existing_created(workspace: Workspace) -> dict[str, str]:
    created: dict[str, str] = {}
    for path in workspace.card_files():
        if path.name == "index.md":
            continue
        try:
            raw = path.read_text(encoding="utf-8")
            data, _, error = split_front_matter(raw)
        except OSError:
            continue
        if error:
            continue
        card_id = data.get("id")
        created_value = data.get("created")
        if isinstance(card_id, str) and isinstance(created_value, str):
            created[card_id] = created_value
    return created


def _existing_text(workspace: Workspace, relative: str) -> str:
    path = workspace.resolve_read(relative)
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _questions_with_answers(
    questions: list[OpenQuestion],
    ask: Callable[[str], str] | None,
    max_questions: int,
) -> list[OpenQuestion]:
    if ask is None:
        return questions
    result: list[OpenQuestion] = []
    for question in questions[:max_questions]:
        answer = ask(question.question)
        if answer and answer.strip():
            result.append(
                question.model_copy(
                    update={"status": "answered", "answer": answer.strip()}
                )
            )
        else:
            result.append(question)
    return result


def run_ingest(
    *,
    workspace: Workspace,
    config: ProjectConfig,
    provider: LLMProvider,
    source_dir: Path,
    run: RunContext,
    dry_run: bool = False,
    interactive: bool = False,
    ask: Callable[[str], str] | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> IngestOutcome:
    outcome = IngestOutcome(run_id=run.run_id, dry_run=dry_run)
    try:
        if on_progress:
            on_progress(f"Инвентаризация: {source_dir}")
        inventory = build_inventory(source_dir, max_file_size_mb=config.max_file_size_mb)
        run.manifest.source_hashes = {doc.file: doc.sha256 for doc in inventory.documents}
        run.save()
        run.write_json(
            "inventory.json",
            {
                "source_dir": str(source_dir),
                "documents": [document.model_dump(mode="json") for document in inventory.documents],
                "skipped": inventory.skipped,
                "warnings": inventory.warnings,
            },
        )
        outcome.warnings.extend(inventory.warnings)

        if on_progress:
            on_progress(f"Разбор документов: {len(inventory.documents)}")
        corpus = parse_corpus(source_dir, inventory.documents, config)
        outline = build_outline(corpus.documents, corpus.blocks)
        run.write_json("outline.json", outline)
        outcome.warnings.extend(corpus.warnings)

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
        if not chunks:
            raise IngestError("Не удалось сформировать ни одного чанка из исходных документов")
        outcome.chunk_count = len(chunks)
        run.write_json(
            "chunks.json",
            {
                "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
                "count": len(chunks),
            },
        )

        if on_progress:
            on_progress(f"Извлечение фактов: {len(chunks)} чанков")

        def progress(chunk: object, fact_count: int) -> None:
            if on_progress:
                chunk_id = getattr(chunk, "chunk_id", "?")
                on_progress(f"  {chunk_id}: фактов {fact_count}")

        extraction = extract_all(
            chunks=chunks,
            blocks=corpus.blocks,
            provider=provider,
            run=run,
            config=config,
            progress=progress,
        )
        outcome.fact_count = len(extraction.facts)
        outcome.warnings.extend(extraction.warnings)
        run.write_json(
            "facts.json",
            {
                "facts": [fact.model_dump(mode="json") for fact in extraction.facts],
                "count": len(extraction.facts),
                "skipped_chunks": extraction.skipped_chunks,
            },
        )
        if not extraction.facts:
            raise IngestError(
                "LLM не извлекла ни одного факта. Карточки не записаны. "
                "Проверьте source_dir, промт extract.md и ответы провайдера."
            )

        grouped: GroupedResult = group_facts(extraction.facts, config)
        outcome.conflict_count = len(grouped.conflicts)
        if not grouped.cards:
            raise IngestError("Не удалось сгруппировать факты в карточки")


        questions = _questions_with_answers(
            grouped.questions,
            ask if interactive else None,
            config.max_questions,
        )
        outcome.question_count = len(questions)
        if interactive and ask is not None:
            run.write_json(
                "answers.json",
                [
                    {
                        "question": question.question,
                        "status": question.status,
                        "answer": question.answer,
                    }
                    for question in questions
                ],
            )

        created_map = _existing_created(workspace)
        rendered: RenderedCards = render_all(
            grouped.cards,
            config,
            questions,
            created_map=created_map,
        )
        outcome.card_count = len(rendered.cards)
        if not rendered.cards:
            raise IngestError("После рендеринга не осталось карточек")

        new_ids = {card.id for card in rendered.cards}
        existing_ids = {
            str(data.get("id"))
            for path in workspace.card_files()
            for data, _, error in [split_front_matter(_existing_text(workspace, workspace.relative(path)))]
            if not error and path.name != "index.md" and data.get("id")
        }
        stale = sorted(existing_ids - new_ids - {config.project_id})
        manual_items: list[ManualReviewItem] = [
            *inventory.manual_review,
            *corpus.manual_review,
            *extraction.manual_review,
        ]
        for stale_id in stale:
            manual_items.append(
                ManualReviewItem(
                    file=f"{config.output_dir}/{stale_id}.md",
                    reason="Карточка не входит в новый набор; удаление запрещено, проверьте вручную",
                    kind="stale_card",
                )
            )

        reports = {
            f"{config.reports_dir}/sources.md": render_sources(rendered.cards, corpus.documents),
            f"{config.reports_dir}/open_questions.md": render_open_questions(questions, config),
            f"{config.reports_dir}/conflicts.md": render_conflicts(grouped.conflicts),
            f"{config.reports_dir}/manual_review.md": render_manual_review(
                manual_items, [*inventory.warnings, *corpus.warnings, *extraction.warnings]
            ),
        }
        staged: dict[str, str] = dict(rendered.files)
        staged.update(reports)
        outcome.files = sorted(staged)

        changes = {
            relative: (_existing_text(workspace, relative), content)
            for relative, content in staged.items()
            if _existing_text(workspace, relative) != content
        }
        diff = make_multi_diff(changes)
        run.write_text("changes.diff", diff)

        if dry_run:
            for relative, content in staged.items():
                workspace.atomic_write(run.run_dir / "staged" / relative, content)
            run.write_text(
                "dry_run.md",
                "Dry-run: cards/ и reports/ не изменялись. "
                "Staged-файлы и changes.diff находятся в каталоге прогона.\n",
            )
            run.finish(RunStatus.completed)
            outcome.warnings.append("Dry-run: карточки и отчёты не записаны")
            return outcome

        written = workspace.write_staged(staged)
        for path in written:
            run.record_output(path)
        validation = validate_workspace(workspace, config, apply=False, run=run)
        if validation.errors:
            outcome.warnings.append(
                f"Валидация нашла ошибок: {len(validation.errors)}. См. reports/validation.md"
            )
        run.write_text(
            "summary.md",
            "\n".join(
                [
                    "# Итог ingest",
                    "",
                    f"- Карточек: {outcome.card_count}",
                    f"- Фактов: {outcome.fact_count}",
                    f"- Чанков: {outcome.chunk_count}",
                    f"- Противоречий: {outcome.conflict_count}",
                    f"- Вопросов: {outcome.question_count}",
                    f"- Предупреждений: {len(outcome.warnings)}",
                    "",
                ]
            ),
        )
        run.finish(RunStatus.completed)
        return outcome
    except ProviderError as exc:
        run.finish(RunStatus.failed, [f"Ошибка провайдера: {exc}"])
        raise
    except Exception as exc:
        run.finish(RunStatus.failed, [str(exc)])
        raise

