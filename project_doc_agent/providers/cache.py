"""Кэш ответов LLM: ключ = хэш промта, модели и входного блока."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from project_doc_agent.utils import sha256_text


class ResponseCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def key(self, payload: dict[str, Any]) -> str:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256_text(serialized)

    def path_for(self, key: str) -> Path:
        return self.directory / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        path = self.path_for(key)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict) or not isinstance(raw.get("content"), str):
            return None
        return raw

    def put(self, key: str, payload: dict[str, Any]) -> None:
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(prefix=".cache-", suffix=".tmp", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            temporary.replace(path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
