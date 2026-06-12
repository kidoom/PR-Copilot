from __future__ import annotations

import asyncio
import logging
import traceback
from typing import Any, Callable

logger = logging.getLogger(__name__)

from backend.agent.model.messages import Message, MAX_VISIBLE_DELTA_CHARS, truncate_delta
from backend.agent.runtime.cancellation import CancellationProbe, Cancelled
from backend.agent.runtime.events import (
    MESSAGE_DELTA,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_STARTED,
    RunStatus,
    TOOL_CALL,
    TOOL_RESULT,
    RunEvent,
)
from backend.agent.runtime.final_result import FinalReviewResult, build_final_result, build_fallback_result
from backend.agent.runtime.loop import run_loop
from backend.agent.runtime.memory import (
    AgentKind,
    MemorySessionMeta,
    append_event,
    append_message,
    build_main_session_id,
)
from backend.agent.runtime.memory.store import FileMemoryStore
from backend.agent.runtime.run_manager import RunManager
from backend.agent.tools.registry import ToolRegistry
from backend.deps import AgentDeps
from backend.storage.pr_session.models import AgentSessionRef, AgentSessionsRecord, AgentSessionStatus
from backend.storage.pr_session.run_persistence import (
    persist_agent_sessions,
    persist_event,
    persist_lifecycle_transition,
    persist_result,
)


def _summarize_value(value: Any, *, max_chars: int = 500) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= max_chars else value[:max_chars] + "...[truncated]"
    if isinstance(value, list):
        return [_summarize_value(v, max_chars=max_chars // 2) for v in value[:10]]
    if isinstance(value, dict):
        summarized: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 20:
                summarized["..."] = "truncated"
                break
            summarized[str(key)] = _summarize_value(item, max_chars=max_chars // 2)
        return summarized
    text = str(value)
    return text if len(text) <= max_chars else text[:max_chars] + "...[truncated]"


def _message_to_payload(msg: Message) -> dict[str, Any]:
    """Convert a Message to a serializable payload."""
    content = msg.content
    if isinstance(content, list):
        # ToolUseBlock or ToolResultBlock
        blocks = []
        for block in content:
            if hasattr(block, "tool_use_id") and hasattr(block, "name"):
                # ToolUseBlock
                blocks.append({
                    "tool_use_id": block.tool_use_id,
                    "name": block.name,
                    "input": block.input,
                })
            elif hasattr(block, "tool_use_id"):
                # ToolResultBlock
                blocks.append({
                    "tool_use_id": block.tool_use_id,
                    "content": block.content,
                    "is_error": block.is_error,
                })
        content = blocks
    return {
        "role": msg.role.value,
        "content": content,
    }


def _update_agent_session_status(
    store: Any,
    run_id: str,
    memory_session_id: str,
    status: AgentSessionStatus,
) -> None:
    """Update the status of an agent session reference in the durable store."""
    try:
        record = store.load_agent_sessions(run_id)
        if record is None:
            return
        for ref in record.sessions:
            if ref.memory_session_id == memory_session_id:
                ref.status = status
                from datetime import datetime, timezone
                ref.completed_at = datetime.now(timezone.utc).isoformat()
                break
        store.save_agent_sessions(record)
    except Exception:
        pass  # Best-effort; don't fail the run


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
    workspace_manager: Any = None,
    pr_identity: Any = None,
    token: str | None = None,
) -> dict[str, Any]:
    # Persistence: get the PR session store from deps
    pr_store = deps.pr_session_store

    def _emit(event_type: str, payload: dict[str, Any] | None = None) -> RunEvent:
        event = run_manager.publish_event(run_id, event_type, payload)
        # Persist event to durable storage
        persist_event(pr_store, event)
        if event_sink is not None:
            event_sink(event)
        return event

    def _emit_runtime_event(event_type: str, payload: dict[str, Any]) -> None:
        event_payload = dict(payload)
        if "input" in event_payload:
            event_payload["input_summary"] = _summarize_value(event_payload.pop("input"))
        if "output" in event_payload:
            event_payload["output_summary"] = _summarize_value(event_payload.pop("output"))
        _emit(event_type, event_payload)

    # Create main memory session
    memory_store: FileMemoryStore = deps.memory_store
    session_id = build_main_session_id(run_id, context_id)
    main_session = MemorySessionMeta(
        session_id=session_id,
        run_id=run_id,
        agent_kind=AgentKind.MAIN,
        agent_type="main-agent",
        context_id=context_id,
    )
    memory_store.create_session(main_session)

    # Register main-agent memory session with durable run
    ps_id = pr_store.resolve_run_to_pr_session(run_id)
    if ps_id:
        agent_sessions_record = AgentSessionsRecord(
            run_id=run_id,
            sessions=[
                AgentSessionRef(
                    memory_session_id=session_id,
                    agent_kind="main",
                    agent_type="main-agent",
                    status=AgentSessionStatus.ACTIVE,
                ),
            ],
        )
        persist_agent_sessions(pr_store, run_id, agent_sessions_record)

    # Bail out early if run was already cancelled before we started
    session = run_manager.get_run(run_id)
    if session.status in (RunStatus.CANCELLED, RunStatus.COMPLETED, RunStatus.FAILED):
        return {"error": f"Run already in terminal state: {session.status.value}"}

    run_manager.mark_running(run_id)
    persist_lifecycle_transition(pr_store, run_id, RunStatus.RUNNING)
    _emit(RUN_STARTED, {"context_id": context_id})

    # Create cancellation probe for this run (task 4.1)
    cancellation_probe = CancellationProbe()
    run_manager.register_cancellation_probe(run_id, cancellation_probe)

    # Append run started event to memory
    append_event(memory_store, session_id, {
        "event_type": RUN_STARTED,
        "context_id": context_id,
    })

    runtime = None
    model = None
    try:
        model = deps.new_model()
        runtime = await asyncio.to_thread(
            deps.build_main_runtime,
            model=model,
            task_plan=task_plan,
            pr_context=pr_context,
            repo_root=repo_root,
            parent_session_id=parent_session_id or run_id,
            run_id=run_id,
            workspace_manager=workspace_manager,
            pr_identity=pr_identity,
            token=token,
            on_runtime_event=_emit_runtime_event,
            cancellation_probe=cancellation_probe,
        )

        # Planner tasks are trusted server-side data. Dispatch them directly so
        # the main model does not need to echo a potentially large TaskPlan.
        task_results = []
        planned_tasks = task_plan.get("tasks", [])
        if planned_tasks:
            tool_use_id = "planner_dispatch"
            _emit_runtime_event(TOOL_CALL, {
                "agent_kind": "main",
                "agent_type": "main-agent",
                "task_id": "",
                "child_session_id": "",
                "tool_name": "task",
                "tool_use_id": tool_use_id,
                "input": {
                    "dispatch_mode": "planner_bound",
                    "task_count": len(planned_tasks),
                },
            })
            task_results = await runtime.task_tool.run_many(task_plan=task_plan)
            status_counts: dict[str, int] = {}
            for task_result in task_results:
                status = task_result.get("status", "")
                status_counts[status] = status_counts.get(status, 0) + 1
            _emit_runtime_event(TOOL_RESULT, {
                "agent_kind": "main",
                "agent_type": "main-agent",
                "task_id": "",
                "child_session_id": "",
                "tool_name": "task",
                "tool_use_id": tool_use_id,
                "output": {
                    "dispatched": len(task_results),
                    "status_counts": status_counts,
                },
                "is_error": False,
            })

        messages = deps.build_main_messages(task_plan, task_results=task_results)

        # Append initial messages to memory
        for msg in messages:
            append_message(memory_store, session_id, _message_to_payload(msg))

        # Define callback to persist each message during run_loop
        def on_message(msg: Message) -> None:
            append_message(memory_store, session_id, _message_to_payload(msg))

        # Get compression config and profile
        from backend.agent.runtime.compression.compact_prompts import CompactProfile, select_compact_profile
        from backend.agent.runtime.compression.config import CompressionConfig

        compression_config = deps.compression_config if hasattr(deps, 'compression_config') else CompressionConfig.default()
        compression_profile = select_compact_profile("main")

        def _emit_text_delta(text: str) -> None:
            """Emit a bounded message.delta RunEvent for visible assistant text."""
            bounded = truncate_delta(text, MAX_VISIBLE_DELTA_CHARS)
            if bounded:
                _emit(MESSAGE_DELTA, {"text": bounded, "agent_type": "main-agent"})

        try:
            result = await run_loop(
                model=model,
                tool_registry=ToolRegistry(),
                messages=messages,
                max_steps=max_steps,
                on_message=on_message,
                on_tool_call=lambda payload: _emit_runtime_event(TOOL_CALL, payload),
                on_tool_result=lambda payload: _emit_runtime_event(TOOL_RESULT, payload),
                on_text_delta=_emit_text_delta,
                agent_kind="main",
                agent_type="main-agent",
                cancellation_probe=cancellation_probe,
                # Compression parameters
                session_id=session_id,
                memory_store=memory_store,
                compression_config=compression_config,
                compression_profile=compression_profile,
            )
        except (Cancelled, asyncio.CancelledError):
            raise
        except Exception as exc:
            if not task_results:
                raise
            logger.error(
                "Run %s synthesis failed: %s — falling back to partial results",
                run_id,
                exc,
                exc_info=True,
            )
            fallback = build_fallback_result(task_results=task_results)
            fallback.summary = "审查完成，但最终综合分析失败，仅展示子任务结果。"
            fallback.uncertainties.append(
                f"最终综合分析失败: {type(exc).__name__}: {exc}. 仅展示子任务结果。"
            )
            output_payload = fallback.to_dict()
            run_manager.complete_run(run_id, result=output_payload)
            persist_lifecycle_transition(pr_store, run_id, RunStatus.COMPLETED)
            _update_agent_session_status(pr_store, run_id, session_id, AgentSessionStatus.COMPLETED)

            ps_id = pr_store.resolve_run_to_pr_session(run_id)
            if ps_id:
                persist_result(
                    pr_store, run_id, ps_id, "completed",
                    findings=output_payload.get("findings", []),
                )

            append_event(memory_store, session_id, {
                "event_type": RUN_COMPLETED,
                "output": output_payload,
            })
            return output_payload

        # Try to parse main-agent synthesis as structured JSON (task 5.9)
        main_synthesis_parsed = None
        try:
            from backend.agent.runtime.review_result import parse_review_result, validate_review_result
            parsed_review = parse_review_result(result.output)
            if parsed_review:
                # Validate findings before promoting (defense-in-depth)
                validation_errors = validate_review_result(parsed_review)
                if not validation_errors:
                    main_synthesis_parsed = parsed_review.to_dict()
                else:
                    # Invalid main synthesis - use fallback without findings
                    main_synthesis_parsed = {
                        "status": parsed_review.status.value,
                        "summary": parsed_review.summary,
                        "findings": [],  # Drop invalid findings
                        "uncertainties": parsed_review.uncertainties,
                        "notes": parsed_review.notes,
                    }
        except Exception:
            pass

        # Build normalized final result (task 5.1, 5.11)
        final_result = build_final_result(
            task_results=task_results,
            raw_output=result.output,
            steps=len(result.steps),
            stopped_by_max_steps=result.stopped_by_max_steps,
            token_usage={
                "input_tokens": result.token_usage.input_tokens,
                "output_tokens": result.token_usage.output_tokens,
            },
            main_synthesis_parsed=main_synthesis_parsed,
        )

        output_payload = final_result.to_dict()
        run_manager.complete_run(run_id, result=output_payload)
        persist_lifecycle_transition(pr_store, run_id, RunStatus.COMPLETED)

        # Update main-agent session status to completed
        _update_agent_session_status(pr_store, run_id, session_id, AgentSessionStatus.COMPLETED)

        # Persist terminal result
        ps_id = pr_store.resolve_run_to_pr_session(run_id)
        if ps_id:
            persist_result(
                pr_store, run_id, ps_id, "completed",
                findings=output_payload.get("findings", []),
                coverage=output_payload.get("coverage", {}),
                usage=output_payload.get("usage", {}),
            )

        # Append completion event to memory
        append_event(memory_store, session_id, {
            "event_type": RUN_COMPLETED,
            "output": output_payload,
        })

        return output_payload

    except (Cancelled, asyncio.CancelledError):
        # Cancellation observed - transition to cancelled (task 4.3)
        run_manager.observe_cancellation(run_id)
        persist_lifecycle_transition(pr_store, run_id, RunStatus.CANCELLED)
        _update_agent_session_status(pr_store, run_id, session_id, AgentSessionStatus.CANCELLED)

        ps_id = pr_store.resolve_run_to_pr_session(run_id)
        if ps_id:
            persist_result(pr_store, run_id, ps_id, "cancelled")

        append_event(memory_store, session_id, {
            "event_type": "run.cancelled",
        })
        return {"error": "cancelled"}

    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        tb = traceback.format_exc()
        run_manager.fail_run(run_id, error=error_msg)
        persist_lifecycle_transition(pr_store, run_id, RunStatus.FAILED, error_summary=error_msg)
        _update_agent_session_status(pr_store, run_id, session_id, AgentSessionStatus.FAILED)

        ps_id = pr_store.resolve_run_to_pr_session(run_id)
        if ps_id:
            persist_result(pr_store, run_id, ps_id, "failed", error_summary=error_msg)

        # Append failure event to memory
        append_event(memory_store, session_id, {
            "event_type": RUN_FAILED,
            "error": error_msg,
        })

        return {"error": error_msg}

    finally:
        if runtime is not None:
            runtime.cleanup_workspace()
        close_model = getattr(model, "close", None)
        if callable(close_model):
            try:
                await close_model()
            except Exception:
                pass
