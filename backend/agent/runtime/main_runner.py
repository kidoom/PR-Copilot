from __future__ import annotations

import traceback
from typing import Any, Callable

from backend.agent.model.messages import Message
from backend.agent.runtime.events import (
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_STARTED,
    TOOL_CALL,
    TOOL_RESULT,
    RunEvent,
)
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
from backend.deps import AgentDeps


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
    def _emit(event_type: str, payload: dict[str, Any] | None = None) -> RunEvent:
        event = run_manager.publish_event(run_id, event_type, payload)
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

    run_manager.mark_running(run_id)
    _emit(RUN_STARTED, {"context_id": context_id})

    # Append run started event to memory
    append_event(memory_store, session_id, {
        "event_type": RUN_STARTED,
        "context_id": context_id,
    })

    runtime = None
    try:
        model = deps.new_model()
        messages = deps.build_main_messages(task_plan)
        runtime = deps.build_main_runtime(
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
        )

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

        result = await run_loop(
            model=model,
            tool_registry=runtime.tool_registry,
            messages=messages,
            max_steps=max_steps,
            on_message=on_message,
            on_tool_call=lambda payload: _emit_runtime_event(TOOL_CALL, payload),
            on_tool_result=lambda payload: _emit_runtime_event(TOOL_RESULT, payload),
            agent_kind="main",
            agent_type="main-agent",
            # Compression parameters
            session_id=session_id,
            memory_store=memory_store,
            compression_config=compression_config,
            compression_profile=compression_profile,
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

        # Append completion event to memory
        append_event(memory_store, session_id, {
            "event_type": RUN_COMPLETED,
            "output": output_payload,
        })

        return output_payload

    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        tb = traceback.format_exc()
        run_manager.fail_run(run_id, error=error_msg)

        # Append failure event to memory
        append_event(memory_store, session_id, {
            "event_type": RUN_FAILED,
            "error": error_msg,
        })

        return {"error": error_msg}

    finally:
        if runtime is not None:
            runtime.cleanup_workspace()
