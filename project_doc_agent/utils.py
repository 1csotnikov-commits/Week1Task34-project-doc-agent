"""Общие утилиты: slug, хэши, даты, нормализация текста."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import TypeVar

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

_TRANSLIT = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "c",
    "ч": "ch",
    "ш": "sh",
    "щ": "sch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}

T = TypeVar("T")


def is_slug(value: str) -> bool:
    return bool(SLUG_RE.fullmatch(value))


def transliterate(text: str) -> str:
    lowered = text.lower().replace("«", " ").replace("»", " ")
    return "".join(_TRANSLIT.get(char, char) for char in lowered)


def slugify(text: str, fallback: str = "topic", max_length: int = 60) -> str:
    """Стабильный латинский slug из произвольной строки."""
    transliterated = transliterate(text)
    slug = re.sub(r"[^a-z0-9]+", "-", transliterated).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug:
        slug = fallback
    if slug and not slug[0].isalnum():
        slug = f"t-{slug}"
    return slug[:max_length].strip("-") or fallback


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()


def normalize_title_key(text: str) -> str:
    """Ключ для сопоставления заголовков: без номера, пунктуации и регистра."""
    cleaned = re.sub(r"^\s*\d+(?:\.\d+)*\.?\s*", "", text)
    cleaned = re.sub(r"[^\w]+", " ", cleaned.casefold(), flags=re.UNICODE)
    return normalize_ws(cleaned)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: object, chunk_size: int = 1024 * 1024) -> str:
    from pathlib import Path

    file_path = Path(path)  # type: ignore[arg-type]
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def utc_now_iso() -> str:
    return utc_now().isoformat(timespec="seconds")


def today_iso() -> str:
    return utc_now().date().isoformat()


def safe_posix_relative(path: str) -> str:
    """Нормализованный относительный POSIX-путь без выхода вверх."""
    normalized = path.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError(f"Недопустимый относительный путь: {path!r}")
    return pure.as_posix()


def dedupe(items: Iterable[T]) -> list[T]:
    seen: set[int] = set()
    result: list[T] = []
    for item in items:
        marker = id(item) if not isinstance(item, (str, int, float, tuple)) else hash(item)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(item)
    return result


def dedupe_strings(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        value = normalize_ws(item)
        key = value.casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def truncate(text: str, limit: int, marker: str = "…") -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: max(0, limit - len(marker))] + marker


def extract_json_object(text: str) -> str:
    """Выделить первый JSON-объект/массив из ответа модели."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    starts = [index for index in (stripped.find("{"), stripped.find("[")) if index >= 0]
    if not starts:
        return stripped
    start = min(starts)
    opening = stripped[start]
    closing = "}" if opening == "{" else "]"
    end = stripped.rfind(closing)
    if end < start:
        return stripped[start:]
    return stripped[start : end + 1]
