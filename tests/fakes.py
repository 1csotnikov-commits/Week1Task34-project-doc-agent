"""Тестовый провайдер без сети."""

from __future__ import annotations

import json
import re
from typing import Any

from project_doc_agent.providers.base import LLMResponse

BLOCK_RE = re.compile(r"^\[([^\]]+)\] \| ([^|]+) \|", re.MULTILINE)


class FakeProvider:
    name = "deepseek"
    model = "fake-model"

    def __init__(
        self,
        *,
        plan_steps: list[dict[str, Any]] | None = None,
        fail_facts: bool = False,
        confidence: str = "high",
    ) -> None:
        self.calls = 0
        self.plan_calls = 0
        self.plan_steps = plan_steps
        self.fail_facts = fail_facts
        self.confidence = confidence

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: Any,
        purpose: str,
        cache_scope: str = "",
    ) -> tuple[Any, LLMResponse]:
        self.calls += 1
        if schema.__name__ == "Plan":
            self.plan_calls += 1
            if self.plan_steps is not None:
                payload = {
                    "done": self.plan_calls > 1,
                    "reason": "test plan",
                    "steps": self.plan_steps if self.plan_calls == 1 else [],
                }
            else:
                payload = {
                    "done": self.plan_calls > 1,
                    "reason": "test plan",
                    "steps": [
                        {"tool": "list_files", "args": {"glob": "*.md"}},
                        {"tool": "outline", "args": {}},
                        {"tool": "extract", "args": {}},
                    ]
                    if self.plan_calls == 1
                    else [],
                }
        else:
            payload = {"facts": [] if self.fail_facts else self._facts(user)}
        content = json.dumps(payload, ensure_ascii=False)
        return (
            schema.model_validate(payload),
            LLMResponse(
                content=content,
                provider=self.name,
                model=self.model,
                prompt_tokens=100,
                completion_tokens=50,
                duration_ms=5,
                cost_usd=0.0,
            ),
        )

    def _facts(self, user: str) -> list[dict[str, Any]]:
        file_match = re.search(r"^Файл: (.+)$", user, re.MULTILINE)
        section_match = re.search(r"^Раздел: (.+)$", user, re.MULTILINE)
        file = file_match.group(1).strip() if file_match else "unknown.md"
        section = section_match.group(1).strip() if section_match else "(без раздела)"
        blocks = BLOCK_RE.findall(user)
        content_blocks = [
            (block_id, kind.strip())
            for block_id, kind in blocks
            if kind.strip() not in {"heading"} and block_id
        ]
        if not content_blocks:
            return []
        lowered = user.casefold()
        if "createshipmentorder" in lowered:
            topic, title, kind = "shipment-order", "Передача заказа на отгрузку", "flow"
            objects = ["Заказ на отгрузку"]
        elif "номенклатура" in lowered or "справочник" in lowered:
            topic, title, kind = "nomenklatura", "Передача справочника «Номенклатура»", "integration_object"
            objects = ["Номенклатура"]
        elif "авторизац" in lowered:
            topic, title, kind = "authorization", "Авторизация в системах обмена", "auth"
            objects = ["Пользователь обмена"]
        elif "ошиб" in lowered or "мониторинг" in lowered:
            topic, title, kind = "error-handling", "Ошибки и мониторинг", "error_handling"
            objects = ["Пакет обмена"]
        else:
            topic, title, kind = "general", "Общее описание интеграции", "other"
            objects = []
        direction = "none"
        if any(marker in user for marker in ("КИС → WMS", "КИС>WMS", "из КИС в WMS")):
            direction = "КИС>WMS"
        elif any(marker in user for marker in ("WMS → КИС", "WMS>КИС", "WMS → КИС")):
            direction = "WMS>КИС"
        refs = [
            {"file": file, "section": section, "block_id": block_id}
            for block_id, _ in content_blocks[:2]
        ]
        detail = section if section != "(без раздела)" else file
        return [
            {
                "fact_id": "fake",
                "card_topic": topic,
                "kind": kind,
                "title": title,
                "summary": f"Факт из раздела «{detail}».",
                "details": [f"Источник: {file}, раздел: {section}."],
                "systems": ["КИС", "WMS"],
                "direction": direction,
                "methods": ["createShipmentOrder"] if "createshipmentorder" in lowered else [],
                "objects": objects,
                "source_refs": refs,
                "confidence": self.confidence,
                "open_questions": [],
            }
        ]
