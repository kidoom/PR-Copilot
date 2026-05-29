from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Awaitable

from .agent_def import AgentDefinition, AgentRegistry, UnknownAgentError
from .sub_agent import SubAgentResult

Runner = Callable[[AgentDefinition, str, int], Awaitable[SubAgentResult]]

DEFAULT_MAX_STEPS = 10
ABSOLUTE_MAX_STEPS = 50


class TaskToolError(Exception):
    pass


class TaskTool:
    def __init__(
        self,
        agent_registry: AgentRegistry,
        runner: Runner,
    ) -> None:
        self._agent_registry = agent_registry
        self._runner = runner

    async def run(
        self,
        *,
        prompt: str | None = None,
        task: dict[str, Any] | None = None,
        agent_type: str = "default",
        max_steps: int | None = None,
    ) -> SubAgentResult:
        effective_prompt = prompt
        if effective_prompt is None and task is not None:
            effective_prompt = task.get("prompt") or task.get("description")
        if not effective_prompt or not effective_prompt.strip():
            raise TaskToolError("TaskTool requires a non-empty prompt or task payload.")

        agent_def = self._agent_registry.resolve(agent_type)

        steps = max_steps if max_steps is not None else agent_def.default_max_steps
        steps = max(1, min(steps, ABSOLUTE_MAX_STEPS))

        return await self._runner(agent_def, effective_prompt, steps)
