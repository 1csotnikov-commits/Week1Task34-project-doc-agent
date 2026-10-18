"""Агентный прогон по цели пользователя."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from project_doc_agent.agent.executor import Executor, Observation
from project_doc_agent.agent.planner import Planner
from project_doc_agent.config import ProjectConfig
from project_doc_agent.errors import PlanError, ProviderError
from project_doc_agent.models import Fact, OpenQuestion, RunStatus
from project_doc_agent.providers.base import LLMProvider
from project_doc_agent.runs.manifest import RunContext
from project_doc_agent.tools.registry import ToolLayer
from project_doc_agent.validation.validator import validate_workspace
from project_doc_agent.workspace import Workspace


@dataclass
class AgentOutcome:
    run_id: str
    completed: bool = False
    steps: int = 0
    facts: int = 0
    written_files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)


def _load_state(run: RunContext) -> dict[str, Any]:
    return run.read_json("state.json") or {}


def _save_state(
    run: RunContext,
    *,
    goal: str,
    steps_done: int,
    observations: list[dict[str, Any]],
    staged: dict[str, str],
    plan_rounds: int,
) -> None:
    run.write_json(
        "state.json",
        {
            "goal": goal,
            "steps_done": steps_done,
            "observations": observations,
            "staged": staged,
            "plan_rounds": plan_rounds,
        },
    )


def _write_staged_to_run(run: RunContext, workspace: Workspace, staged: dict[str, str]) -> None:
    for relative, content in staged.items():
        workspace.atomic_write(run.run_dir / "staged" / relative, content)


def run_agent(
    *,
    workspace: Workspace,
    config: ProjectConfig,
    provider: LLMProvider,
    source_dir: Path,
    goal: str,
    run: RunContext,
    dry_run: bool = False,
    interactive: bool = False,
    ask: Callable[[str], str] | None = None,
    on_progress: Callable[[str], None] | None = None,
    resume: bool = False,
) -> AgentOutcome:
    state = _load_state(run) if resume else {}
    if resume:
        if not state:
            raise PlanError(f"Нет state.json для продолжения прогона {run.run_id}")
        goal = str(state.get("goal") or goal)
    steps_done = int(state.get("steps_done") or 0)
    observations: list[dict[str, Any]] = list(state.get("observations") or [])
    plan_rounds = int(state.get("plan_rounds") or 0)

    tools = ToolLayer(
        workspace=workspace,
        config=config,
        source_dir=source_dir,
        provider=provider,
        run=run,
        dry_run=dry_run,
    )
    tools.staged = dict(state.get("staged") or {})
    if resume:
        raw_facts = run.read_json("tool_facts.json") or []
        tools.facts = [Fact.model_validate(item) for item in raw_facts if isinstance(item, dict)]
    planner = Planner(provider, run, config, tools.json_schema(), tools.available_tools)
    executor = Executor(tools)
    outcome = AgentOutcome(run_id=run.run_id)
    completed = False
    last_signature: str | None = None

    try:
        while steps_done < config.max_steps and plan_rounds < config.max_steps:
            plan = planner.make_plan(goal, observations, config.max_steps - steps_done)
            plan_rounds += 1
            if plan.done:
                completed = True
                if plan.reason:
                    outcome.warnings.append(f"Планировщик: {plan.reason}")
                break
            if not plan.steps:
                outcome.warnings.append("Планировщик вернул пустой план без done=true")
                break
            signature = repr([(step.tool, sorted(step.args.items(), key=lambda item: item[0])) for step in plan.steps])
            if signature == last_signature:
                outcome.warnings.append("Планировщик повторил тот же план; прогон остановлен")
                break
            last_signature = signature
            for step in plan.steps:
                if steps_done >= config.max_steps:
                    break
                steps_done += 1
                observation = executor.execute(step, steps_done)
                observations.append(observation.as_dict())
                outcome.observations.append(observation)
                _save_state(
                    run,
                    goal=goal,
                    steps_done=steps_done,
                    observations=observations,
                    staged=tools.staged,
                    plan_rounds=plan_rounds,
                )
                if on_progress:
                    status = "ok" if observation.ok else f"ошибка: {observation.error}"
                    on_progress(f"Шаг {steps_done}: {observation.tool} — {status}")
                if not observation.ok and observation.error:
                    outcome.warnings.append(observation.error)
            if steps_done >= config.max_steps:
                outcome.warnings.append(f"Достигнут лимит шагов: {config.max_steps}")
                break

        if not completed:
            completed = True
            produced = any(
                observation.tool in {"build_cards", "write_card"} and observation.ok
                for observation in outcome.observations
            )
            if produced:
                outcome.warnings.append(
                    "Лимит шагов исчерпан после сборки документации; изменения подготовлены и проверены."
                )
            else:
                outcome.warnings.append(
                    "Цель не подтверждена планировщиком, но лимит шагов исчерпан."
                )

        if tools.staged:
            validation = tools.validate()
            errors = int(validation.data.get("errors", 0) or 0)
            if errors:
                _write_staged_to_run(run, workspace, tools.staged)
                raise PlanError(
                    f"Подготовленные карточки не прошли валидацию: ошибок {errors}. "
                    "Файлы не записаны; см. staged/ в каталоге прогона."
                )
            tools.diff()
            if dry_run:
                _write_staged_to_run(run, workspace, tools.staged)
                outcome.warnings.append("Dry-run: карточки не записаны в cards/")
            else:
                written = workspace.write_staged(tools.staged)
                for path in written:
                    run.record_output(path)
                    outcome.written_files.append(workspace.relative(path))
                try:
                    final_validation = validate_workspace(workspace, config, apply=False, run=run)
                    if final_validation.errors:
                        outcome.warnings.append(
                            f"Валидация после записи: ошибок {len(final_validation.errors)}; "
                            "см. reports/validation.md"
                        )
                except Exception as exc:  # noqa: BLE001 - отчёт не должен ломать прогон
                    outcome.warnings.append(f"Не удалось сформировать reports/validation.md: {exc}")

        questions = _fact_questions(tools.facts, config)
        answers = _ask_questions(questions, ask if interactive else None, config.max_questions)
        if answers:
            run.write_json("answers.json", answers)
            if not dry_run:
                _append_answers(workspace, answers)

        outcome.steps = steps_done
        outcome.facts = len(tools.facts)
        _write_agent_report(run, goal, outcome, completed, dry_run)
        run.finish(RunStatus.completed)
        outcome.completed = completed
        return outcome
    except (PlanError, ProviderError) as exc:
        if tools.staged:
            _write_staged_to_run(run, workspace, tools.staged)
        run.finish(RunStatus.failed, [str(exc)])
        raise
    except Exception as exc:
        if tools.staged:
            _write_staged_to_run(run, workspace, tools.staged)
        run.finish(RunStatus.failed, [str(exc)])
        raise



def _fact_questions(facts: list[Fact], config: ProjectConfig) -> list[OpenQuestion]:
    questions: list[OpenQuestion] = []
    seen: set[str] = set()
    for fact in facts:
        for question in fact.open_questions:
            key = question.casefold()
            if key in seen:
                continue
            seen.add(key)
            questions.append(
                OpenQuestion(
                    question=question,
                    why=f"Уточнение по факту «{fact.title}»",
                    sources=fact.source_refs[:3],
                )
            )
    return questions[: config.max_questions]


def _ask_questions(
    questions: list[OpenQuestion],
    ask: Callable[[str], str] | None,
    max_questions: int,
) -> list[dict[str, Any]]:
    if ask is None:
        return []
    answers: list[dict[str, Any]] = []
    for question in questions[:max_questions]:
        answer = ask(question.question)
        answers.append(
            {
                "question": question.question,
                "answer": answer.strip() if answer else "",
                "status": "answered" if answer and answer.strip() else "open",
            }
        )
    return answers


def _append_answers(workspace: Workspace, answers: list[dict[str, Any]]) -> None:
    path = workspace.reports_dir / "open_questions.md"
    lines = [
        "",
        "## Ответы пользователя (агентный режим)",
        "",
    ]
    for item in answers:
        marker = "x" if item["status"] == "answered" else " "
        lines.append(f"- [{marker}] {item['question']}")
        if item["answer"]:
            lines.append(f"  - Ответ: {item['answer']}")
    content = "\n".join(lines) + "\n"
    if path.is_file():
        existing = path.read_text(encoding="utf-8")
        workspace.atomic_write(workspace.relative(path), existing.rstrip() + "\n" + content)
    else:
        workspace.atomic_write(workspace.relative(path), "# Открытые вопросы\n" + content)


def _write_agent_report(
    run: RunContext,
    goal: str,
    outcome: AgentOutcome,
    completed: bool,
    dry_run: bool,
) -> None:
    lines = [
        "# Отчёт агентского прогона",
        "",
        f"Цель: {goal}",
        f"Статус: {'completed' if completed else 'failed'}",
        f"Шагов: {outcome.steps}",
        f"Фактов: {outcome.facts}",
        f"Записанных файлов: {len(outcome.written_files)}",
        f"Dry-run: {'да' if dry_run else 'нет'}",
        "",
        "## Наблюдения",
        "",
    ]
    if outcome.observations:
        for observation in outcome.observations:
            lines.extend(
                [
                    f"### Шаг {observation.step}: `{observation.tool}`",
                    "",
                    f"- Статус: {'ok' if observation.ok else 'error'}",
                ]
            )
            if observation.error:
                lines.append(f"- Ошибка: {observation.error}")
            if observation.output:
                lines.extend(["", "```text", observation.output[:3000], "```"])
            lines.append("")
    else:
        lines.extend(["Наблюдений нет.", ""])
    if outcome.written_files:
        lines.extend(["## Записанные файлы", ""])
        lines.extend(f"- `{path}`" for path in outcome.written_files)
        lines.append("")
    if outcome.warnings:
        lines.extend(["## Предупреждения", ""])
        lines.extend(f"- {warning}" for warning in outcome.warnings)
        lines.append("")
    run.write_text("report.md", "\n".join(lines))

