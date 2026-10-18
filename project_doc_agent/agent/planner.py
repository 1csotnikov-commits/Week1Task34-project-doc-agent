"""Планировщик агентного режима."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from project_doc_agent.config import ProjectConfig
from project_doc_agent.errors import PlanError
from project_doc_agent.models import LLMCallRecord
from project_doc_agent.providers.base import LLMProvider
from project_doc_agent.resources import read_prompt
from project_doc_agent.runs.manifest import RunContext
from project_doc_agent.utils import sha256_text, truncate


class PlanStep(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class Plan(BaseModel):
    done: bool = False
    reason: str = ""
    steps: list[PlanStep] = Field(default_factory=list)


class Planner:
    def __init__(
        self,
        provider: LLMProvider,
        run: RunContext,
        config: ProjectConfig,
        tool_schema: dict[str, Any],
        available_tools: list[str],
    ) -> None:
        self.provider = provider
        self.run = run
        self.config = config
        self.tool_schema = tool_schema
        self.available_tools = available_tools

    def make_plan(
        self,
        goal: str,
        observations: list[dict[str, Any]],
        remaining_steps: int,
    ) -> Plan:
        if self.run.manifest.input_tokens + self.run.manifest.output_tokens >= self.config.max_tokens_total:
            raise PlanError(
                f"Достигнут лимит токенов {self.config.max_tokens_total}; прогон остановлен"
            )
        system = read_prompt("planner.md")
        system += "\n\nДоступные инструменты и JSON-схемы аргументов:\n"
        system += json.dumps(self.tool_schema, ensure_ascii=False, sort_keys=True)
        observation_lines = []
        for observation in observations[-20:]:
            observation_lines.append(
                json.dumps(
                    {
                        "step": observation.get("step"),
                        "tool": observation.get("tool"),
                        "ok": observation.get("ok"),
                        "output": truncate(str(observation.get("output", "")), 2500),
                        "error": truncate(str(observation.get("error") or ""), 800),
                    },
                    ensure_ascii=False,
                )
            )
        user = "\n".join(
            [
                f"Цель: {goal}",
                f"Осталось шагов: {remaining_steps}",
                "Уже выполненные шаги и наблюдения:",
                *observation_lines,
                "",
                "Верни JSON-план. Если цель достигнута, done=true и steps=[].",
            ]
        )
        payload, response = self.provider.complete_json(
            system=system,
            user=user,
            schema=Plan,
            purpose="plan",
            cache_scope=f"planner-v1:{self.config.profile.value}",
        )
        self.run.add_llm_call(
            LLMCallRecord(
                provider=response.provider,
                model=response.model,
                purpose="plan",
                prompt_sha256=sha256_text(system + "\n" + user),
                input_tokens=response.prompt_tokens,
                output_tokens=response.completion_tokens,
                cost_usd=response.cost_usd,
                duration_ms=response.duration_ms,
                cached=response.cached,
                schema_name="Plan",
            )
        )
        for step in payload.steps:
            if step.tool not in self.available_tools:
                raise PlanError(
                    f"План содержит неизвестный инструмент {step.tool!r}. "
                    f"Доступны: {', '.join(self.available_tools)}"
                )
            if not isinstance(step.args, dict):
                raise PlanError(f"Аргументы шага {step.tool!r} должны быть объектом")
        if len(payload.steps) > max(0, remaining_steps):
            payload.steps = payload.steps[:remaining_steps]
        return payload
