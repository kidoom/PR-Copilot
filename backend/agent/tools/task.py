from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Awaitable

from backend.agent.runtime.agent_def import AgentRegistry
from backend.agent.runtime.review_result import ReviewResult, parse_review_result, validate_review_result
from backend.agent.runtime.sub_agent import SubAgentResult
from backend.agent.tools.protocol import RiskLevel

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
        max_concurrent_tasks: int = 4,
    ) -> None:
        self._runner = runner
        self._agent_types = agent_types or (agent_registry.names() if agent_registry is not None else ["default"])
        if not self._agent_types:
            self._agent_types = ["default"]
        self._max_concurrent_tasks = max(1, int(max_concurrent_tasks))

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
                "tasks": {
                    "type": "array",
                    "description": "Planner task list to dispatch to matching subagents",
                    "items": {"type": "object"},
                },
                "routes": {
                    "type": "array",
                    "description": "Planner routes used to map task_type/route_key to agent_type and max_steps",
                    "items": {"type": "object"},
                },
                "task_plan": {
                    "type": "object",
                    "description": "Full planner TaskPlan payload containing tasks and routes",
                },
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

    @property
    def requires_consent(self) -> bool:
        return False

    async def call(self, input: dict[str, Any]) -> str:
        task_plan = input.get("task_plan")
        tasks = input.get("tasks")
        routes = input.get("routes")
        prompt = input.get("prompt")
        agent_type = input.get("agent_type") or self._agent_types[0]
        max_steps = input.get("max_steps")
        task = input.get("task")

        try:
            if task_plan is not None or tasks is not None:
                results = await self.run_many(
                    task_plan=task_plan,
                    tasks=tasks,
                    routes=routes,
                    max_steps=max_steps,
                )
                return json.dumps({
                    "dispatched": len(results),
                    "results": results,
                })

            result = await self.run(
                prompt=prompt,
                task=task,
                agent_type=agent_type,
                max_steps=max_steps,
            )

            # Parse and validate structured review result
            parsed = parse_review_result(result.output)
            validation_errors = validate_review_result(parsed) if parsed else ["Failed to parse JSON"]
            is_valid = parsed is not None and len(validation_errors) == 0

            return json.dumps({
                "output": result.output,
                "agent_type": result.agent_type,
                "child_session_id": result.child_session_id,
                "memory_session_id": result.memory_session_id,
                "steps": len(result.steps),
                "stopped_by_max_steps": result.stopped_by_max_steps,
                "parsed_result": parsed.to_dict() if parsed else None,
                "parse_status": "valid" if is_valid else "invalid",
                "validation_errors": validation_errors if not is_valid else [],
            })
        except TaskToolError as e:
            return json.dumps({"error": str(e)})

    async def run_many(
        self,
        *,
        task_plan: dict[str, Any] | None = None,
        tasks: list[dict[str, Any]] | None = None,
        routes: list[dict[str, Any]] | None = None,
        max_steps: int | None = None,
    ) -> list[dict[str, Any]]:
        plan_context_id = ""
        if task_plan is not None:
            tasks = task_plan.get("tasks", tasks)
            routes = task_plan.get("routes", routes)
            plan_context_id = task_plan.get("context_id", "")

        if not isinstance(tasks, list) or not tasks:
            raise TaskToolError("TaskTool requires a non-empty tasks list for batch dispatch.")

        route_index = _build_route_index(routes or [])
        results: list[dict[str, Any] | None] = [None] * len(tasks)
        semaphore = asyncio.Semaphore(self._max_concurrent_tasks)

        async def _dispatch_one(index: int, raw_task: dict[str, Any]) -> None:
            async with semaphore:
                route = _resolve_route(raw_task, route_index)
                effective_agent_type = (
                    raw_task.get("agent_type")
                    or route.get("agent_type")
                    or _agent_type_from_task_type(raw_task.get("task_type", ""))
                    or self._agent_types[0]
                )
                task_max_steps = max_steps if max_steps is not None else route.get("max_steps")
                task_payload = _build_task_payload(raw_task, route)

                # Inject context_id from task_plan if not already present
                if plan_context_id and "context_id" not in task_payload:
                    task_payload["context_id"] = plan_context_id

                prompt = _build_dispatch_prompt(task_payload)

                try:
                    result = await self.run(
                        prompt=prompt,
                        task=task_payload,
                        agent_type=effective_agent_type,
                        max_steps=task_max_steps,
                    )
                except Exception as exc:
                    results[index] = {
                        "index": index,
                        "task_id": task_payload.get("task_id", ""),
                        "task_type": task_payload.get("task_type", ""),
                        "agent_type": effective_agent_type,
                        "status": "error",
                        "error": str(exc),
                    }
                    return

                # Parse and validate structured review result
                parsed = parse_review_result(result.output)
                validation_errors = validate_review_result(parsed) if parsed else ["Failed to parse JSON"]
                is_valid = parsed is not None and len(validation_errors) == 0

                results[index] = {
                    "index": index,
                    "task_id": task_payload.get("task_id", ""),
                    "task_type": task_payload.get("task_type", ""),
                    "agent_type": result.agent_type,
                    "child_session_id": result.child_session_id,
                    "memory_session_id": result.memory_session_id,
                    "steps": len(result.steps),
                    "stopped_by_max_steps": result.stopped_by_max_steps,
                    "status": "ok" if is_valid else "invalid",
                    "output": result.output,
                    "parsed_result": parsed.to_dict() if parsed else None,
                    "parse_status": "valid" if is_valid else "invalid",
                    "validation_errors": validation_errors if not is_valid else [],
                }

        pending: list[Awaitable[None]] = []

        for index, raw_task in enumerate(tasks):
            if not isinstance(raw_task, dict):
                results[index] = {
                    "index": index,
                    "status": "error",
                    "error": "Task entry must be an object.",
                }
                continue
            pending.append(_dispatch_one(index, raw_task))

        if pending:
            await asyncio.gather(*pending)

        return [r for r in results if r is not None]

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
            task=task,
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


def _build_route_index(routes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for route in routes:
        if not isinstance(route, dict):
            continue
        task_type = route.get("task_type")
        route_key = route.get("route_key")
        if task_type:
            index[f"task_type:{task_type}"] = route
        if route_key:
            index[f"route_key:{route_key}"] = route
    return index


def _resolve_route(task: dict[str, Any], route_index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    route_key = task.get("route_key")
    if route_key and f"route_key:{route_key}" in route_index:
        return route_index[f"route_key:{route_key}"]
    task_type = task.get("task_type")
    if task_type and f"task_type:{task_type}" in route_index:
        return route_index[f"task_type:{task_type}"]
    return {}


def _agent_type_from_task_type(task_type: str) -> str:
    if not task_type:
        return ""
    return f"{task_type.replace('_', '-')}-agent"


def _build_task_payload(task: dict[str, Any], route: dict[str, Any]) -> dict[str, Any]:
    payload = dict(task)
    target = payload.get("target")
    if isinstance(target, dict):
        if "target_files" not in payload:
            payload["target_files"] = target.get("files", [])
        if "target_directories" not in payload:
            payload["target_directories"] = target.get("directories", [])
        if "target_symbols" not in payload:
            payload["target_symbols"] = target.get("symbols", [])
        if "target_keywords" not in payload:
            payload["target_keywords"] = target.get("keywords", [])
    if route:
        payload.setdefault("route", route)
        payload.setdefault("output_schema", route.get("output_schema", {}))
    return payload


def _format_list(values: Any) -> str:
    if not values:
        return "- none"
    if isinstance(values, list):
        return "\n".join(f"- {v}" for v in values)
    return f"- {values}"


def _build_dispatch_prompt(task: dict[str, Any]) -> str:
    return "\n".join([
        f"Task ID: {task.get('task_id', '')}",
        f"Task type: {task.get('task_type', '')}",
        f"Intent: {task.get('intent', '')}",
        f"Priority: {task.get('priority', '')}",
        "",
        "Target files:",
        _format_list(task.get("target_files") or task.get("target")),
        "",
        "Queries:",
        _format_list(task.get("queries")),
        "",
        f"Expected output: {task.get('expected_output', '')}",
        f"Fallback: {task.get('fallback', '')}",
        "",
        "Use the available repo-context tools to gather evidence. Start with todo_write, verify the repo context, then finish with your final JSON review result matching the required schema.",
    ])
