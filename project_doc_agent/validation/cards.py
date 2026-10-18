"""Разбор Markdown-карточек и front matter."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from project_doc_agent.workspace import Workspace


@dataclass
class ParsedCard:
    path: Path
    relative: str
    data: dict[str, Any]
    body: str
    raw: str
    error: str | None = None


def split_front_matter(text: str) -> tuple[dict[str, Any], str, str | None]:
    normalized = text.lstrip("\ufeff")
    lines = normalized.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, normalized, "Нет YAML front matter"
    closing = None
    for index in range(1, len(lines)):
        if lines[index].strip() in {"---", "..."}:
            closing = index
            break
    if closing is None:
        return {}, normalized, "Не найден закрывающий --- front matter"
    raw_front = "".join(lines[1:closing])
    body = "".join(lines[closing + 1 :]).lstrip("\r\n")
    try:
        data = yaml.safe_load(raw_front)
    except yaml.YAMLError as exc:
        return {}, body, f"Некорректный YAML front matter: {exc}"
    if not isinstance(data, dict):
        return {}, body, "Front matter должен быть YAML-объектом"
    return data, body, None


def parse_card_file(workspace: Workspace, path: Path) -> ParsedCard:
    relative = workspace.relative(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return ParsedCard(path, relative, {}, "", "", f"Не удалось прочитать карточку: {exc}")
    data, body, error = split_front_matter(raw)
    return ParsedCard(path=path, relative=relative, data=data, body=body, raw=raw, error=error)


def parse_cards(workspace: Workspace) -> list[ParsedCard]:
    cards: list[ParsedCard] = []
    for path in workspace.card_files():
        if path.name == "index.md":
            continue
        cards.append(parse_card_file(workspace, path))
    return cards


def compose_card(data: dict[str, Any], body: str) -> str:
    front = yaml.safe_dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=1000,
    )
    return f"---\n{front}---\n\n{body.strip()}\n"
