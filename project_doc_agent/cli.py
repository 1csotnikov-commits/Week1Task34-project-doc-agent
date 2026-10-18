"""CLI `docagent`."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import NoReturn, Optional

import typer

from project_doc_agent import __version__
from project_doc_agent.agent.runner import run_agent
from project_doc_agent.config import (
    ProjectConfig,
    effective_source_dir,
    load_project_config,
    load_providers,
    make_project_config,
    save_project_config,
)
from project_doc_agent.errors import DocAgentError
from project_doc_agent.index.fts import (
    SearchIndex,
    card_records,
    save_search_report,
    source_records,
)
from project_doc_agent.ingest.inventory import build_inventory
from project_doc_agent.ingest.parser import parse_corpus
from project_doc_agent.ingest.pipeline import run_ingest
from project_doc_agent.models import Profile, RunStatus
from project_doc_agent.providers.factory import build_provider
from project_doc_agent.runs.manifest import RunContext
from project_doc_agent.utils import is_slug
from project_doc_agent.validation.cards import split_front_matter
from project_doc_agent.validation.validator import validate_workspace
from project_doc_agent.workspace import Workspace

app = typer.Typer(
    name="docagent",
    help="Документация проекта из DOCX/MD/TXT в Markdown для Obsidian и RAG.",
    add_completion=False,
    no_args_is_help=True,
)

DEFAULT_DATA_ROOT = Path(os.environ.get("DOCAGENT_DATA_ROOT", r"C:\DataProject\project-doc-agent"))


def _safe_console() -> None:
    """Не падать на символах, отсутствующих в кодовой странице консоли Windows."""
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except (AttributeError, OSError):
            pass
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                if stream.isatty():
                    stream.reconfigure(encoding="utf-8", errors="replace")
                else:
                    stream.reconfigure(errors="replace")
            except (AttributeError, OSError, ValueError):
                pass


_safe_console()


def _fail(exc: Exception | str, code: int = 2) -> NoReturn:
    typer.secho(f"Ошибка: {exc}", err=True, fg=typer.colors.RED)
    raise typer.Exit(code)


def _load(workspace: Optional[Path], *, check_source: bool = True) -> tuple[Workspace, ProjectConfig]:
    ws = Workspace.require(workspace or Path.cwd())
    config = load_project_config(ws.root, check_source=check_source)
    ws.configure(config.output_dir, config.reports_dir)
    return ws, config


def _prompt_slug(label: str) -> str:
    while True:
        value = typer.prompt(label).strip()
        if is_slug(value):
            return value
        typer.secho("Ожидается slug: латиница, цифры и дефисы, например `marta`.", err=True)


def _prompt_text(label: str) -> str:
    while True:
        value = typer.prompt(label).strip()
        if value:
            return value
        typer.secho("Значение не может быть пустым.", err=True)


def _prompt_bases() -> list[str]:
    while True:
        raw = typer.prompt("Базы (slug через запятую)").strip()
        bases = [item.strip() for item in raw.split(",") if item.strip()]
        if bases and all(is_slug(item) for item in bases):
            return bases
        typer.secho("Укажите хотя бы одну базу в slug-формате.", err=True)


def _select_provider(default: str = "deepseek") -> str:
    providers = load_providers()
    names = [provider.name for provider in providers.providers]
    typer.echo(f"Доступные провайдеры: {', '.join(names)}")
    value = typer.prompt("Провайдер", default=default).strip()
    if value not in names:
        typer.secho(f"Провайдер {value!r} не найден в config/providers.yaml", err=True)
        raise typer.Exit(2)
    return value


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", help="Показать версию и выйти", is_eager=True),
) -> None:
    if version:
        typer.echo(f"docagent {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


@app.command()
def init(
    workspace: Optional[Path] = typer.Option(None, "--workspace", help="Каталог проекта"),
    client: Optional[str] = typer.Option(None, "--client", help="Slug клиента"),
    client_name: Optional[str] = typer.Option(None, "--client-name", help="Название клиента"),
    project: Optional[str] = typer.Option(None, "--project", help="Slug проекта"),
    project_name: Optional[str] = typer.Option(None, "--project-name", help="Название проекта"),
    bases: Optional[list[str]] = typer.Option(None, "--base", help="Slug базы (можно несколько)"),
    profile: Optional[str] = typer.Option(None, "--profile", help="task или integration"),
    provider: Optional[str] = typer.Option(None, "--provider", help="Провайдер LLM"),
    source_dir: Optional[Path] = typer.Option(None, "--source-dir", help="Папка исходных документов"),
    non_interactive: bool = typer.Option(False, "--non-interactive", help="Не задавать вопросов"),
) -> None:
    """Создать docagent.yaml и структуру каталогов проекта."""
    root = (workspace or Path.cwd()).expanduser().resolve()
    if root.exists() and not root.is_dir():
        _fail(f"Путь workspace не является папкой: {root}")
    if (root / "docagent.yaml").is_file():
        _fail(f"docagent.yaml уже существует: {root / 'docagent.yaml'}. Изменение не выполняется.")
    root.mkdir(parents=True, exist_ok=True)

    profiles = {item.value for item in Profile}
    provider_names = [item.name for item in load_providers().providers]

    if non_interactive:
        missing = [
            name
            for name, value in (
                ("--client", client),
                ("--client-name", client_name),
                ("--project", project),
                ("--project-name", project_name),
                ("--base", bases),
                ("--source-dir", source_dir),
            )
            if not value
        ]
        if missing:
            _fail(f"Для --non-interactive обязательны: {', '.join(missing)}")
        selected_profile = (profile or "integration").strip()
        selected_provider = (provider or "deepseek").strip()
    else:
        client = client or _prompt_slug("Slug клиента")
        client_name = client_name or _prompt_text("Название клиента")
        project = project or _prompt_slug("Slug проекта")
        project_name = project_name or _prompt_text("Название проекта")
        bases = bases or _prompt_bases()
        selected_profile = profile or typer.prompt("Профиль (task/integration)", default="integration").strip()
        if provider is None:
            selected_provider = _select_provider()
        else:
            selected_provider = provider
        if source_dir is None:
            while True:
                raw = typer.prompt("Папка исходных документов", default=str(Path.cwd())).strip()
                candidate = Path(raw).expanduser().resolve()
                if candidate.is_dir():
                    source_dir = candidate
                    break
                typer.secho(f"Папка не найдена: {candidate}", err=True)

    if selected_profile not in profiles:
        _fail(f"Недопустимый профиль {selected_profile!r}; ожидается task или integration")
    if selected_provider not in provider_names:
        _fail(f"Провайдер {selected_provider!r} не найден; доступны: {', '.join(provider_names)}")
    assert client and client_name and project and project_name and bases and source_dir
    source_resolved = source_dir.expanduser().resolve()
    if not source_resolved.is_dir():
        _fail(f"source_dir не существует или не является папкой: {source_resolved}")

    try:
        config = make_project_config(
            client=client,
            client_name=client_name,
            project=project,
            project_name=project_name,
            bases=list(bases),
            profile=selected_profile,
            provider=selected_provider,
            source_dir=str(source_resolved).replace("\\", "/"),
        )
    except ValueError as exc:
        _fail(f"Некорректная конфигурация: {exc}")
    ws = Workspace(root)
    ws.configure(config.output_dir, config.reports_dir)
    ws.ensure_layout()
    save_project_config(root, config)
    typer.echo(f"Создан проект: {root}")
    typer.echo(f"docagent.yaml: {root / 'docagent.yaml'}")
    typer.echo(f"Карточки: {ws.cards_dir}")
    typer.echo(f"Отчёты: {ws.reports_dir}")
    typer.echo("Следующий шаг: docagent ingest")



@app.command()
def ingest(
    workspace: Optional[Path] = typer.Option(None, "--workspace", help="Каталог проекта"),
    profile: Optional[str] = typer.Option(None, "--profile", help="task или integration"),
    source_dir: Optional[Path] = typer.Option(None, "--source-dir", help="Переопределить source_dir"),
    provider: Optional[str] = typer.Option(None, "--provider", help="Переопределить провайдера"),
    interactive: bool = typer.Option(False, "--interactive", help="Задавать вопросы в CLI"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Не писать cards/ и reports/"),
) -> None:
    """Папка документов → карточки, отчёты и diff."""
    try:
        ws, config = _load(workspace, check_source=source_dir is None)
        if profile:
            config = config.model_copy(update={"profile": Profile(profile)})
        source = effective_source_dir(config, ws.root, source_dir)
        selected_provider = provider or config.provider
        provider_config = load_providers().by_name(selected_provider)
    except (DocAgentError, ValueError) as exc:
        _fail(exc)
    run = RunContext.create(
        ws,
        "ingest",
        provider=provider_config.name,
        model=provider_config.model,
        temperature=provider_config.temperature,
        max_tokens=provider_config.max_tokens,
        params={
            "source_dir": str(source),
            "profile": config.profile.value,
            "dry_run": dry_run,
            "interactive": interactive,
        },
        prompt_names=("extract.md",),
    )
    save_raw = config.save_llm_raw or os.environ.get("DOCAGENT_SAVE_LLM_RAW", "").lower() in {"1", "true", "yes"}
    try:
        llm = build_provider(
            config,
            name=selected_provider,
            cache_dir=ws.cache_dir,
            raw_dir=(run.run_dir / "llm_raw") if save_raw else None,
        )
    except DocAgentError as exc:
        run.finish(RunStatus.failed, [str(exc)])
        _fail(exc, code=1)
    try:
        outcome = run_ingest(
            workspace=ws,
            config=config,
            provider=llm,
            source_dir=source,
            run=run,
            dry_run=dry_run,
            interactive=interactive,
            ask=(lambda question: typer.prompt(question, default="")) if interactive else None,
            on_progress=typer.echo,
        )
    except DocAgentError as exc:
        _fail(exc, code=1)
    typer.echo("")
    typer.echo(f"Прогон: {outcome.run_id}")
    typer.echo(f"Карточек: {outcome.card_count}; фактов: {outcome.fact_count}; чанков: {outcome.chunk_count}")
    typer.echo(f"Конфликтов: {outcome.conflict_count}; вопросов: {outcome.question_count}")
    typer.echo(f"Файлов подготовлено: {len(outcome.files)}")
    if dry_run:
        typer.secho("Dry-run: cards/ и reports/ не изменены.", fg=typer.colors.YELLOW)
    for warning in dict.fromkeys(outcome.warnings):
        typer.secho(f"Предупреждение: {warning}", fg=typer.colors.YELLOW)



@app.command()
def search(
    query: str = typer.Option(..., "--query", help="Поисковый запрос"),
    workspace: Optional[Path] = typer.Option(None, "--workspace", help="Каталог проекта"),
    regex: bool = typer.Option(False, "--regex", help="Регулярное выражение"),
    context: int = typer.Option(1, "--context", min=0, max=10, help="Контекст в блоках"),
    include_cards: bool = typer.Option(False, "--include-cards", help="Искать и по карточкам"),
) -> None:
    """Поиск по исходным документам и (опционально) карточкам."""
    run: RunContext | None = None
    try:
        ws, config = _load(workspace)
        source = config.source_path(ws.root)
        run = RunContext.create(
            ws,
            "search",
            params={
                "query": query,
                "regex": regex,
                "context": context,
                "include_cards": include_cards,
                "source_dir": str(source),
            },
        )
        inventory = build_inventory(source, max_file_size_mb=config.max_file_size_mb)
        corpus = parse_corpus(source, inventory.documents, config)
        records, warnings = source_records(source, inventory.documents, config)
        if include_cards:
            records.extend(card_records(ws, config))
        index = SearchIndex(ws)
        result = index.search(
            records,
            query,
            regex=regex,
            context=context,
            include_cards=include_cards,
        )
        result.warnings.extend([*inventory.warnings, *corpus.warnings, *warnings])
        report_path = save_search_report(ws, run, result)
        run.finish(RunStatus.completed)
    except DocAgentError as exc:
        if run is not None and run.manifest.status is RunStatus.running:
            run.finish(RunStatus.failed, [str(exc)])
        _fail(exc, code=1)
    assert run is not None
    typer.echo(f"Найдено совпадений: {len(result.matches)} в {len(result.files)} файлах")
    for match in result.matches[:10]:
        typer.echo(f"- {match.file} | {match.section} | {match.block_id}")
        typer.echo(f"  {match.snippet[:220]}")
    typer.echo(f"Отчёт: {report_path}")
    typer.echo(f"matches.json: {run.run_dir / 'matches.json'}")



@app.command()
def validate(
    workspace: Optional[Path] = typer.Option(None, "--workspace", help="Каталог проекта"),
    profile: Optional[str] = typer.Option(None, "--profile", help="task или integration"),
    apply: bool = typer.Option(False, "--apply", help="Применить diff после успешной валидации"),
    diff: bool = typer.Option(False, "--diff", help="Показать diff в консоли"),
) -> None:
    """Проверить карточки, сформировать отчёт и diff."""
    run: RunContext | None = None
    try:
        ws, config = _load(workspace)
        if profile:
            config = config.model_copy(update={"profile": Profile(profile)})
        run = RunContext.create(
            ws,
            "validate",
            params={"apply": apply, "profile": config.profile.value},
        )
        result = validate_workspace(ws, config, apply=apply, run=run)
        run.finish(RunStatus.completed)
    except (DocAgentError, ValueError) as exc:
        if run is not None and run.manifest.status is RunStatus.running:
            run.finish(RunStatus.failed, [str(exc)])
        _fail(exc, code=1)
    assert run is not None
    typer.echo(f"Карточек проверено: {result.cards_checked}")
    typer.echo(f"Ошибок: {len(result.errors)}; предупреждений: {len(result.warnings)}")
    typer.echo(f"Отчёт: {ws.reports_dir / 'validation.md'}")
    typer.echo(f"changes.diff: {run.run_dir / 'changes.diff'}")
    if diff and result.diff:
        typer.echo("")
        typer.echo(result.diff)
    if apply:
        if result.applied:
            typer.secho(f"Применено файлов: {len(result.applied_files)}", fg=typer.colors.GREEN)
        else:
            typer.secho(f"Diff не применён: {result.apply_error}", fg=typer.colors.YELLOW)
    if result.errors or result.apply_error:
        raise typer.Exit(1)



@app.command()
def run(
    goal: str = typer.Option(..., "--goal", help="Цель пользователя"),
    workspace: Optional[Path] = typer.Option(None, "--workspace", help="Каталог проекта"),
    interactive: bool = typer.Option(False, "--interactive", help="Задавать вопросы в CLI"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Не записывать карточки"),
    resume: Optional[str] = typer.Option(None, "--resume", help="Продолжить прогон RUN_ID"),
    provider: Optional[str] = typer.Option(None, "--provider", help="Переопределить провайдера"),
) -> None:
    """Агентный режим: пользователь задаёт цель, агент выбирает инструменты."""
    try:
        ws, config = _load(workspace)
        source = config.source_path(ws.root)
        selected_provider = provider or config.provider
        if resume:
            run = RunContext.open(ws, resume)
            state = run.read_json("state.json") or {}
            goal = str(state.get("goal") or goal)
            selected_provider = provider or run.manifest.provider or selected_provider
        else:
            provider_config = load_providers().by_name(selected_provider)
            run = RunContext.create(
                ws,
                "run",
                provider=provider_config.name,
                model=provider_config.model,
                temperature=provider_config.temperature,
                max_tokens=provider_config.max_tokens,
                goal=goal,
                params={
                    "source_dir": str(source),
                    "dry_run": dry_run,
                    "interactive": interactive,
                },
                prompt_names=("planner.md", "extract.md"),
            )
        provider_config = load_providers().by_name(selected_provider)
        run.manifest.provider = provider_config.name
        run.manifest.model = provider_config.model
        run.manifest.temperature = provider_config.temperature
        run.manifest.max_tokens = provider_config.max_tokens
        run.save()
        save_raw = config.save_llm_raw or os.environ.get("DOCAGENT_SAVE_LLM_RAW", "").lower() in {"1", "true", "yes"}
        try:
            llm = build_provider(
                config,
                name=selected_provider,
                cache_dir=ws.cache_dir,
                raw_dir=(run.run_dir / "llm_raw") if save_raw else None,
            )
        except DocAgentError as exc:
            run.finish(RunStatus.failed, [str(exc)])
            _fail(exc, code=1)
        outcome = run_agent(
            workspace=ws,
            config=config,
            provider=llm,
            source_dir=source,
            goal=goal,
            run=run,
            dry_run=dry_run,
            interactive=interactive,
            ask=(lambda question: typer.prompt(question, default="")) if interactive else None,
            on_progress=typer.echo,
            resume=bool(resume),
        )
    except DocAgentError as exc:
        _fail(exc, code=1)
    typer.echo("")
    typer.echo(f"Прогон: {outcome.run_id}")
    typer.echo(f"Шагов: {outcome.steps}; фактов: {outcome.facts}; файлов записано: {len(outcome.written_files)}")
    typer.echo(f"Отчёт: {run.run_dir / 'report.md'}")
    if dry_run:
        typer.secho("Dry-run: изменения не записаны в cards/.", fg=typer.colors.YELLOW)
    for warning in dict.fromkeys(outcome.warnings):
        typer.secho(f"Предупреждение: {warning}", fg=typer.colors.YELLOW)



@app.command()
def projects(
    root: Path = typer.Option(DEFAULT_DATA_ROOT, "--root", help="Корень данных проектов"),
) -> None:
    """Список проектов в каталоге данных."""
    root = root.expanduser().resolve()
    if not root.is_dir():
        typer.secho(f"Каталог не найден: {root}", fg=typer.colors.YELLOW)
        return
    entries: list[tuple[str, ProjectConfig, int]] = []
    for config_path in sorted(root.rglob("docagent.yaml"), key=lambda item: item.as_posix().casefold()):
        try:
            config = load_project_config(config_path.parent, check_source=False)
        except DocAgentError as exc:
            typer.secho(f"Пропущен {config_path}: {exc}", err=True)
            continue
        ws = Workspace(config_path.parent)
        ws.configure(config.output_dir, config.reports_dir)
        card_count = 0
        for path in ws.card_files():
            if path.name == "index.md":
                continue
            try:
                raw = path.read_text(encoding="utf-8")
                data, _, error = split_front_matter(raw)
            except OSError:
                continue
            if not error and data.get("type") != "project":
                card_count += 1
        entries.append((config_path.parent.relative_to(root).as_posix(), config, card_count))
    if not entries:
        typer.echo("Проекты не найдены.")
        return
    typer.echo(
        f"{'Проект':24} {'Клиент':10} {'Название клиента':24} {'Slug':16} "
        f"{'Профиль':12} {'Провайдер':10} {'Карт.':5} Название"
    )
    for relative, config, card_count in entries:
        typer.echo(
            f"{relative:24} {config.client:10} {config.client_name:24} {config.project:16} "
            f"{config.profile.value:12} {config.provider:10} {card_count:<5} {config.project_name}"
        )

