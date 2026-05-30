from __future__ import annotations

import traceback
from typing import Any, Callable

from backend.agent.runtime.events import (
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_STARTED,
    RunEvent,
)
from backend.agent.runtime.loop import run_loop
from backend.agent.runtime.run_manager import RunManager
from backend.deps import AgentDeps


async def run_main_agent(
    *,
    run_id: str,
    context_id: str,
    task_plan: dict[str, Any],
    pr_context: Any,
    repo_root: str,
    deps: AgentDeps,
    run_manager: RunManager,
    max_steps: int = 10,
    parent_session_id: str | None = None,
    event_sink: Callable[[RunEvent], None] | None = None,
) -> dict[str, Any]:
    def _emit(event_type: str, payload: dict[str, Any] | None = None) -> RunEvent:
        event = run_manager.publish_event(run_id, event_type, payload)
        if event_sink is not None:
            event_sink(event)
        return event

    run_manager.mark_running(run_id)
    _emit(RUN_STARTED, {"context_id": context_id})

    try:
        model = deps.new_model()
        messages = deps.build_main_messages(task_plan)
        runtime = deps.build_main_runtime(
            model=model,
            task_plan=task_plan,
            pr_context=pr_context,
            repo_root=repo_root,
            parent_session_id=parent_session_id or run_id,
        )

        result = await run_loop(
            model=model,
            tool_registry=runtime.tool_registry,
            messages=messages,
            max_steps=max_steps,
        )

        output_payload = {
            "output": result.output,
            "steps": len(result.steps),
            "stopped_by_max_steps": result.stopped_by_max_steps,
            "token_usage": {
                "input_tokens": result.token_usage.input_tokens,
                "output_tokens": result.token_usage.output_tokens,
            },
        }
        run_manager.complete_run(run_id, result=output_payload)
        return output_payload

    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        tb = traceback.format_exc()
        run_manager.fail_run(run_id, error=error_msg)
        return {"error": error_msg}
