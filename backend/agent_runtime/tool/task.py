from __future__ import annotations

import json
from typing import Any, Callable, Awaitable

from backend.agent_runtime.runtime.agent_def import AgentRegistry
from backend.agent_runtime.runtime.sub_agent import SubAgentResult
from backend.agent_runtime.tool.protocol import RiskLevel

Runner = Callable[..., Awaitable[SubAgentResult]]

DEFAULT_MAX_STEPS = 10
ABSOLUTE_MAX_STEPS = 50


class TaskToolError(Exception):
    pass


class TaskTool:
    def __init__(
        self,
        runner: Runner,
        agent_registry: AgentRegistry | None = None,
        agent_types: list[str] | None = None,
    ) -> None:
        self._runner = runner
        self._agent_types = agent_types or (agent_registry.names() if agent_registry is not None else ["default"])
        if not self._agent_types:
            self._agent_types = ["default"]

    @property
    def name(self) -> str:
        return "task"

    @property
    def description(self) -> str:
        roles = ", ".join(self._agent_types)
        return (
            "Delegate a bounded task to an isolated subagent. The prompt or task "
            "must be self-contained because the subagent starts with fresh context. "
            f"Available agent_type roles: {roles}."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Self-contained prompt for the subagent"},
                "agent_type": {
                    "type": "string",
                    "enum": list(self._agent_types),
                    "description": f"Name of the agent type to delegate to (default: {self._agent_types[0]})",
                },
                "max_steps": {"type": "integer", "description": "Maximum steps for the subagent (clamped to 1-50)"},
                "task": {"type": "object", "description": "Task payload with queries/intent/target as alternative to prompt"},
            },
        }

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.MEDIUM

    @property
    def is_read_only(self) -> bool:
        return False

    @property
    def is_concurrency_safe(self) -> bool:
        return False

    async def call(self, input: dict[str, Any]) -> str:
        prompt = input.get("prompt")
        agent_type = input.get("agent_type") or self._agent_types[0]
        max_steps = input.get("max_steps")
        task = input.get("task")

        try:
            result = await self.run(
                prompt=prompt,
                task=task,
                agent_type=agent_type,
                max_steps=max_steps,
            )
            return json.dumps({
                "output": result.output,
                "agent_type": result.agent_type,
                "child_session_id": result.child_session_id,
                "steps": len(result.steps),
                "stopped_by_max_steps": result.stopped_by_max_steps,
            })
        except TaskToolError as e:
            return json.dumps({"error": str(e)})

    async def run(
        self,
        *,
        prompt: str | None = None,
        task: dict[str, Any] | None = None,
        agent_type: str | None = None,
        max_steps: int | None = None,
    ) -> SubAgentResult:
        effective_prompt = prompt
        if effective_prompt is None and task is not None:
            effective_prompt = (
                task.get("prompt")
                or task.get("description")
                or task.get("intent")
                or _build_prompt_from_queries(task)
            )
        if not effective_prompt or not effective_prompt.strip():
            raise TaskToolError("TaskTool requires a non-empty prompt or task payload.")

        effective_agent_type = agent_type or self._agent_types[0]
        if effective_agent_type not in self._agent_types:
            available = ", ".join(sorted(self._agent_types))
            raise TaskToolError(f"Unknown agent type '{effective_agent_type}'. Available agent types: {available}")

        steps = None
        if max_steps is not None:
            steps = max(1, min(int(max_steps), ABSOLUTE_MAX_STEPS))

        return await self._runner(
            prompt=effective_prompt,
            agent_type=effective_agent_type,
            max_steps=steps,
        )


def _build_prompt_from_queries(task: dict[str, Any]) -> str | None:
    queries = task.get("queries")
    if queries and isinstance(queries, list) and queries:
        return "\n".join(queries)
    task_type = task.get("task_type")
    if task_type:
        target = task.get("target_files") or task.get("target")
        if target:
            return f"Perform {task_type} on {', '.join(target) if isinstance(target, list) else target}"
        return f"Perform {task_type}"
    return None
