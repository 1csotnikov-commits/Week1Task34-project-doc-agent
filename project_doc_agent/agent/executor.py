"""Исполнитель шагов плана."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from project_doc_agent.agent.planner import PlanStep
from project_doc_agent.tools.registry import ToolLayer


@dataclass
class Observation:
    step: int
    tool: str
    ok: bool
    output: str = ""
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "tool": self.tool,
            "ok": self.ok,
            "output": self.output,
            "error": self.error,
        }


class Executor:
    def __init__(self, tools: ToolLayer) -> None:
        self.tools = tools

    def execute(self, step: PlanStep, number: int) -> Observation:
        result = self.tools.execute(step.tool, step.args, step=number)
        return Observation(
            step=number,
            tool=step.tool,
            ok=result.ok,
            output=result.output,
            error=result.error,
        )
