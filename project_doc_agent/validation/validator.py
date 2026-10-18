"""Валидация карточек по стандарту и правилам."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from project_doc_agent.config import ProjectConfig
from project_doc_agent.resources import standard_sections
from project_doc_agent.runs.manifest import RunContext, new_run_id
from project_doc_agent.utils import is_slug, today_iso, truncate
from project_doc_agent.validation.cards import ParsedCard, compose_card, parse_cards, split_front_matter
from project_doc_agent.validation.diff import apply_multi_diff, make_multi_diff
from project_doc_agent.workspace import Workspace

NO_DATA = "Нет данных в источниках."
REQUIRED_FIELDS = (
    "id",
    "client",
    "client_name",
    "project",
    "project_name",
    "bases",
    "title",
    "type",
    "profile",
    "status",
    "risk",
    "sources",
    "review_status",
    "tags",
    "created",
    "updated",
)
CARD_TYPES = {
    "integration",
    "reference",
    "flow",
    "requirement",
    "error",
    "auth",
    "scope",
    "glossary",
    "other",
    "project",
}
REVIEW_STATUSES = {"ok", "insufficient_data", "needs_review"}
PLACEHOLDER_RE = re.compile(r"\b(TODO|TBD|FIXME)\b|заполнить", re.IGNORECASE)
ELLIPSIS_RE = re.compile(r"^\s*(\.{3,}|…)\s*$")
WIKI_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
H2_RE = re.compile(r"^##\s+(.+?)\s*$")


@dataclass
class Issue:
    severity: Literal["error", "warning"]
    code: str
    card_id: str
    path: str
    message: str
    fixable: bool = False


@dataclass
class ValidationResult:
    cards_checked: int = 0
    issues: list[Issue] = field(default_factory=list)
    remaining: list[Issue] = field(default_factory=list)
    diff: str = ""
    report_path: str = "reports/validation.md"
    applied: bool = False
    applied_files: list[str] = field(default_factory=list)
    apply_error: str | None = None

    @property
    def errors(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def remaining_errors(self) -> list[Issue]:
        return [issue for issue in self.remaining if issue.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors


def _section_map(body: str) -> list[dict[str, Any]]:
    lines = body.splitlines()
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_code = False
    for index, line in enumerate(lines):
        if line.strip().startswith("```"):
            in_code = not in_code
        if in_code:
            continue
        match = H2_RE.match(line)
        if match:
            if current is not None:
                current["end"] = index
            current = {
                "title": match.group(1).strip(),
                "start": index,
                "content_start": index + 1,
                "end": len(lines),
            }
            sections.append(current)
    return sections


def _validate_one(
    parsed: ParsedCard,
    config: ProjectConfig,
    card_ids: set[str],
    file_stems: set[str],
    umbrella_present: bool,
    conflicts_text: str,
) -> list[Issue]:
    issues: list[Issue] = []
    data = parsed.data
    card_id = str(data.get("id") or parsed.path.stem)

    def add(severity: Literal["error", "warning"], code: str, message: str, fixable: bool = False) -> None:
        issues.append(
            Issue(
                severity=severity,
                code=code,
                card_id=card_id,
                path=parsed.relative,
                message=message,
                fixable=fixable,
            )
        )

    if parsed.error:
        add("error", "front_matter", parsed.error)
        return issues
    for field_name in REQUIRED_FIELDS:
        if field_name not in data or data.get(field_name) in (None, "", []):
            add("error", "required_field", f"Отсутствует обязательное поле {field_name!r}")

    for field_name in ("client", "project"):
        value = data.get(field_name)
        if isinstance(value, str) and not is_slug(value):
            add("error", "slug", f"Поле {field_name} должно быть slug: {value!r}")
    card_id_value = data.get("id")
    if isinstance(card_id_value, str) and not is_slug(card_id_value):
        add("error", "slug", f"id должен быть slug: {card_id_value!r}")
    bases = data.get("bases")
    if isinstance(bases, list):
        for base in bases:
            if not isinstance(base, str) or not is_slug(base):
                add("error", "slug", f"Некорректный slug базы: {base!r}")
    if data.get("client") not in (None, config.client):
        add("warning", "client_mismatch", f"client карточки не совпадает с docagent.yaml ({config.client})")
    if data.get("project") not in (None, config.project):
        add("warning", "project_mismatch", f"project карточки не совпадает с docagent.yaml ({config.project})")
    if data.get("profile") not in (None, config.profile.value):
        add("warning", "profile_mismatch", f"profile карточки не совпадает с проектом ({config.profile.value})")

    card_type = data.get("type")
    if isinstance(card_type, str) and card_type not in CARD_TYPES:
        add("error", "type", f"Недопустимый type: {card_type!r}")
    if card_type != "project" and isinstance(card_id_value, str) and isinstance(data.get("client"), str):
        prefix = f"{data.get('client')}-{data.get('project')}-"
        if not card_id_value.startswith(prefix):
            add("error", "id_prefix", f"id карточки должен начинаться с {prefix!r}")

    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        add("error", "sources", "Нет ни одного источника")
    else:
        for source in sources:
            if not isinstance(source, dict) or not source.get("file") or not source.get("block_id"):
                add("error", "sources", "Источник должен содержать file и block_id")

    review_status = data.get("review_status")
    if review_status is not None and review_status not in REVIEW_STATUSES:
        add("error", "review_status", f"Недопустимый review_status: {review_status!r}")
    if review_status in {"needs_review", "insufficient_data"} and not data.get("review_note"):
        add("error", "review_note", "Для review_status != ok нужен review_note", fixable=True)
    risk = data.get("risk")
    if risk is not None and risk not in {"low", "medium", "high"}:
        add("error", "risk", f"Недопустимый risk: {risk!r}")
    if isinstance(data.get("tags"), list) and not data["tags"]:
        add("warning", "tags", "Список tags пуст")
    for field_name in ("created", "updated"):
        value = data.get(field_name)
        if isinstance(value, str) and not DATE_RE.match(value):
            add("warning", "date", f"{field_name} должен быть датой YYYY-MM-DD: {value!r}")
    if card_type != "project" and data.get("parent") != config.project_id:
        add("warning", "parent", f"parent должен ссылаться на umbrella {config.project_id}", fixable=True)


    if card_type != "project":
        required_sections = standard_sections(config.profile.value)
        sections = _section_map(parsed.body)
        titles = [str(section["title"]) for section in sections]
        for section_title in required_sections:
            if section_title not in titles:
                add("error", "missing_section", f"Отсутствует H2-раздел «{section_title}»", fixable=True)
        for title in set(titles):
            if titles.count(title) > 1:
                add("warning", "duplicate_section", f"Раздел «{title}» встречается несколько раз")
        lines = parsed.body.splitlines()
        for section in sections:
            content = "\n".join(lines[int(section["content_start"]) : int(section["end"])]).strip()
            if not content:
                add("error", "empty_section", f"Раздел «{section['title']}» пуст", fixable=True)
        if not umbrella_present:
            add("error", "umbrella", f"Для карточки нет umbrella {config.project_id}")
    else:
        umbrella_titles = [str(section["title"]) for section in _section_map(parsed.body)]
        for section_title in ("Назначение", "Клиент и базы", "Состав карточек", "Источники", "Связанные заметки"):
            if section_title not in umbrella_titles:
                add("warning", "umbrella_section", f"В umbrella отсутствует раздел «{section_title}»")

    heading_matches = re.findall(r"^(#{1,6})\s+", parsed.body, flags=re.MULTILINE)
    if card_type != "project":
        if heading_matches and heading_matches.count("#") != 1:
            add("warning", "h1", "В карточке должен быть ровно один H1")
        if any(len(match) >= 4 for match in heading_matches):
            add("warning", "h4", "В карточке есть H4 и глубже; стандарт рекомендует избегать их")

    match = PLACEHOLDER_RE.search(parsed.body)
    if match:
        add("error", "placeholder", f"Найден placeholder: {match.group(0)!r}")
    for line in parsed.body.splitlines():
        if ELLIPSIS_RE.match(line):
            add("warning", "placeholder", "Найден placeholder из многоточия")
            break
    if isinstance(data.get("title"), str) and PLACEHOLDER_RE.search(data["title"]):
        add("error", "placeholder", "Placeholder в title")

    existing_targets = {target.casefold() for target in card_ids | file_stems | {config.project_id, "index"}}
    for target in WIKI_RE.findall(CODE_BLOCK_RE.sub("", parsed.body)):
        normalized = target.strip().split("/")[-1].removesuffix(".md").casefold()
        if normalized and normalized not in existing_targets:
            add("error", "wiki_link", f"Битая wiki-ссылка: [[{target.strip()}]]")

    if (
        conflicts_text
        and "не обнаружено" not in conflicts_text.casefold()
        and card_type != "project"
        and review_status == "ok"
    ):
        add("warning", "conflicts", "В reports/conflicts.md есть противоречия, а карточка помечена ok")
    return issues



def _section_from_message(message: str) -> str:
    match = re.search(r"«([^»]+)»", message)
    return match.group(1) if match else ""


def _fixed_card(parsed: ParsedCard, issues: list[Issue], config: ProjectConfig) -> str | None:
    if parsed.error:
        return None
    codes = {issue.code for issue in issues if issue.fixable}
    data = dict(parsed.data)
    body = parsed.body
    changed = False
    if "review_note" in codes:
        data["review_note"] = "Требуется проверка: причина не указана в review_note."
        changed = True
    if "parent" in codes and data.get("type") != "project":
        data["parent"] = config.project_id
        changed = True
    lines = body.splitlines()
    empty_titles = {
        _section_from_message(issue.message)
        for issue in issues
        if issue.code == "empty_section"
    }
    empty_sections = [section for section in _section_map(body) if section["title"] in empty_titles]
    for section in sorted(empty_sections, key=lambda item: int(item["content_start"]), reverse=True):
        insert_at = int(section["content_start"])
        lines.insert(insert_at, "")
        lines.insert(insert_at + 1, NO_DATA)
        lines.insert(insert_at + 2, "")
        changed = True
    body = "\n".join(lines)
    missing = [
        _section_from_message(issue.message)
        for issue in issues
        if issue.code == "missing_section"
    ]
    if missing:
        additions = ["", *[line for title in missing for line in (f"## {title}", "", NO_DATA, "")]]
        body = body.rstrip() + "\n" + "\n".join(additions)
        changed = True
    if not changed:
        return None
    return compose_card(data, body)



def _collect_issues(
    cards: list[ParsedCard],
    config: ProjectConfig,
    conflicts_text: str,
    umbrella_present: bool,
) -> list[Issue]:
    issues: list[Issue] = []
    ids: dict[str, list[str]] = {}
    stems = {card.path.stem.casefold() for card in cards}
    for card in cards:
        card_id = str(card.data.get("id") or "")
        if card_id:
            ids.setdefault(card_id, []).append(card.relative)
    card_ids = set(ids)
    for card in cards:
        issues.extend(_validate_one(card, config, card_ids, stems, umbrella_present, conflicts_text))
    for card_id, paths in ids.items():
        if len(paths) > 1:
            for path in paths:
                issues.append(
                    Issue(
                        severity="error",
                        code="duplicate_id",
                        card_id=card_id,
                        path=path,
                        message=f"Дублирующийся id {card_id!r} в {len(paths)} файлах",
                    )
                )
    if not cards:
        issues.append(
            Issue(
                severity="error",
                code="no_cards",
                card_id="",
                path=config.output_dir,
                message="Карточки не найдены. Сначала выполните `docagent ingest`.",
            )
        )
    return issues


def _issue_dict(issue: Issue) -> dict[str, Any]:
    return {
        "severity": issue.severity,
        "code": issue.code,
        "card_id": issue.card_id,
        "path": issue.path,
        "message": issue.message,
        "fixable": issue.fixable,
    }


def _render_validation_report(
    result: ValidationResult,
    config: ProjectConfig,
    *,
    apply_requested: bool,
    remaining_errors: list[Issue],
) -> str:
    lines = [
        "# Валидация карточек",
        "",
        f"Дата: {today_iso()}",
        f"Профиль: `{config.profile.value}`",
        f"Проверено карточек: {result.cards_checked}",
        f"Ошибок: {len(result.errors)}; предупреждений: {len(result.warnings)}",
        "",
    ]
    if result.ok:
        lines.append("Статус: ошибок не найдено.")
    else:
        lines.append("Статус: есть ошибки, требующие исправления.")
    if apply_requested:
        if result.applied:
            lines.append(f"Применение: выполнено, файлов изменено: {len(result.applied_files)}.")
        else:
            lines.append(f"Применение: отклонено. {result.apply_error or ''}".strip())
    lines.extend(
        [
            "",
            f"Ошибок после предлагаемых исправлений: {len(remaining_errors)}",
            "",
        ]
    )
    for title, issues in (("Ошибки", result.errors), ("Предупреждения", result.warnings)):
        lines.extend([f"## {title}", ""])
        if issues:
            lines.extend(["| Карточка | Файл | Код | Сообщение |", "| --- | --- | --- | --- |"])
            for issue in issues:
                escaped_message = issue.message.replace("|", "\\|")
                lines.append(
                    f"| {issue.card_id} | {issue.path} | `{issue.code}` | {escaped_message} |"
                )
        else:
            lines.append("Нет.")
        lines.append("")
    fixable = [issue for issue in result.issues if issue.fixable]
    lines.extend(["## Предлагаемые исправления", ""])
    if fixable:
        for issue in fixable:
            lines.append(f"- `{issue.code}` — {issue.card_id}: {issue.message}")
    else:
        lines.append("Автоматических исправлений нет.")
    lines.extend(["", "## Diff", ""])
    if result.diff:
        lines.extend(["```diff", truncate(result.diff, 40000), "```"])
    else:
        lines.append("Изменений нет.")
    lines.append("")
    if result.applied_files:
        lines.extend(["## Изменённые файлы", ""])
        lines.extend(f"- `{path}`" for path in result.applied_files)
        lines.append("")
    return "\n".join(lines)



def validate_workspace(
    workspace: Workspace,
    config: ProjectConfig,
    *,
    apply: bool = False,
    run: RunContext | None = None,
) -> ValidationResult:
    result = ValidationResult()
    cards = parse_cards(workspace)
    result.cards_checked = len(cards)
    conflicts_path = workspace.reports_dir / "conflicts.md"
    conflicts_text = conflicts_path.read_text(encoding="utf-8") if conflicts_path.is_file() else ""
    umbrella_present = any(
        card.data.get("type") == "project" and card.data.get("id") == config.project_id for card in cards
    )
    issues = _collect_issues(cards, config, conflicts_text, umbrella_present)
    result.issues = issues

    changes: dict[str, tuple[str, str]] = {}
    candidates: list[ParsedCard] = []
    for card in cards:
        card_issues = [issue for issue in issues if issue.path == card.relative]
        fixed = _fixed_card(card, card_issues, config)
        if fixed is None:
            candidates.append(card)
            continue
        data, body, error = split_front_matter(fixed)
        candidates.append(
            ParsedCard(
                path=card.path,
                relative=card.relative,
                data=data,
                body=body,
                raw=fixed,
                error=error,
            )
        )
        changes[card.relative] = (card.raw, fixed)
    result.remaining = _collect_issues(candidates, config, conflicts_text, umbrella_present)
    result.diff = make_multi_diff(changes)
    remaining_errors = result.remaining_errors

    if apply:
        if remaining_errors:
            result.apply_error = (
                f"осталось ошибок: {len(remaining_errors)}. "
                "Исправьте их вручную или повторите с --diff."
            )
        elif not result.diff:
            result.apply_error = "нет предлагаемых исправлений"
        else:
            if run is not None:
                snapshot = run.run_dir / "snapshot"
            else:
                snapshot = workspace.runs_dir / f"validate-{new_run_id()}" / "snapshot"
            snapshot.mkdir(parents=True, exist_ok=True)
            workspace.backup_tree(snapshot)
            applied = apply_multi_diff(workspace, result.diff)
            result.applied = True
            result.applied_files = [workspace.relative(path) for path in applied]
            if run is not None:
                for path in applied:
                    run.record_output(path)

    report = _render_validation_report(
        result, config, apply_requested=apply, remaining_errors=remaining_errors
    )
    workspace.atomic_write(result.report_path, report)
    if run is not None:
        if result.diff:
            run.write_text("changes.diff", result.diff)
        run.write_json(
            "validation.json",
            {
                "cards_checked": result.cards_checked,
                "issues": [_issue_dict(issue) for issue in result.issues],
                "remaining": [_issue_dict(issue) for issue in result.remaining],
                "applied": result.applied,
                "applied_files": result.applied_files,
                "apply_error": result.apply_error,
            },
        )
        run.record_output(workspace.reports_dir / "validation.md")
    return result

