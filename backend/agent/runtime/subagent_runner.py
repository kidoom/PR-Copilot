from __future__ import annotations

import uuid
from typing import Any, Callable, Awaitable, Union

from backend.agent.model.client import ModelClient
from backend.agent.model.messages import Message, Role
from backend.agent.runtime.agent_def import AgentDefinition, AgentRegistry
from backend.agent.runtime.cancellation import CancellationProbe
from backend.agent.runtime.loop import run_loop
from backend.agent.runtime.memory import (
    AgentKind,
    MemorySessionMeta,
    append_event,
    append_message,
    append_todo_state,
    append_evidence_package,
    build_subagent_session_id,
)
from backend.agent.runtime.memory.store import FileMemoryStore
from backend.agent.runtime.results import AgentResult
from backend.agent.runtime.sub_agent import SubAgentResult
from backend.agent.tools.protocol import Tool
from backend.agent.tools.registry import ToolRegistry, filter_tools


def generate_child_session_id(parent_session_id: str) -> str:
    return f"{parent_session_id}.child_{uuid.uuid4().hex[:8]}"


def build_child_messages(agent_def: AgentDefinition, prompt: str) -> list[Message]:
    messages: list[Message] = []
    if agent_def.system_prompt:
        messages.append(Message(role=Role.SYSTEM, content=agent_def.system_prompt))
    messages.append(Message(role=Role.USER, content=prompt))
    return messages


def build_child_tool_registry(child_tools: list[Tool]) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in child_tools:
        registry.register(tool)
    return registry


def _message_to_payload(msg: Message) -> dict[str, Any]:
    """Convert a Message to a serializable payload."""
    content = msg.content
    if isinstance(content, list):
        blocks = []
        for block in content:
            if hasattr(block, "tool_use_id") and hasattr(block, "name"):
                blocks.append({
                    "tool_use_id": block.tool_use_id,
                    "name": block.name,
                    "input": block.input,
                })
            elif hasattr(block, "tool_use_id"):
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


ChildToolFactory = Callable[..., Union[list[Tool], Any]]
RuntimeEventSink = Callable[[str, dict[str, Any]], None]


async def run_subagent(
    *,
    model: ModelClient,
    agent_def: AgentDefinition,
    prompt: str,
    max_steps: int,
    child_session_id: str,
    child_tools: list[Tool],
    memory_store: FileMemoryStore | None = None,
    run_id: str = "",
    context_id: str = "",
    task_id: str = "",
    on_runtime_event: RuntimeEventSink | None = None,
    cancellation_probe: CancellationProbe | None = None,
    # Compression parameters
    compression_config=None,  # CompressionConfig | None
) -> SubAgentResult:
    # Create subagent memory session if store is provided
    subagent_session_id = ""
    if memory_store and run_id:
        subagent_session_id = build_subagent_session_id(
            agent_def.name, run_id, context_id, task_id
        )
        subagent_session = MemorySessionMeta(
            session_id=subagent_session_id,
            run_id=run_id,
            agent_kind=AgentKind.SUBAGENT,
            agent_type=agent_def.name,
            context_id=context_id,
            task_id=task_id,
        )
        memory_store.create_session(subagent_session)

        # Append initial messages
        for msg in build_child_messages(agent_def, prompt):
            append_message(memory_store, subagent_session_id, _message_to_payload(msg))

    messages = build_child_messages(agent_def, prompt)
    tool_registry = build_child_tool_registry(child_tools)

    # Define callback to persist each message during run_loop
    def on_message(msg: Message) -> None:
        if memory_store and subagent_session_id:
            append_message(memory_store, subagent_session_id, _message_to_payload(msg))

    # Get compression profile for subagent
    from backend.agent.runtime.compression.compact_prompts import CompactProfile, select_compact_profile
    compression_profile = select_compact_profile("subagent", agent_def.name)

    result: AgentResult = await run_loop(
        model=model,
        tool_registry=tool_registry,
        messages=messages,
        max_steps=max_steps,
        on_message=on_message if memory_store and subagent_session_id else None,
        on_tool_call=(
            (lambda payload: on_runtime_event("tool.call", payload))
            if on_runtime_event else None
        ),
        on_tool_result=(
            (lambda payload: on_runtime_event("tool.result", payload))
            if on_runtime_event else None
        ),
        agent_kind="subagent",
        agent_type=agent_def.name,
        task_id=task_id,
        child_session_id=child_session_id,
        cancellation_probe=cancellation_probe,
        # Compression parameters
        session_id=subagent_session_id,
        memory_store=memory_store,
        compression_config=compression_config,
        compression_profile=compression_profile,
    )

    return SubAgentResult(
        output=result.output,
        agent_type=agent_def.name,
        child_session_id=child_session_id,
        memory_session_id=subagent_session_id,
        steps=result.steps,
        token_usage=result.token_usage,
        stopped_by_max_steps=result.stopped_by_max_steps,
    )


def build_subagent_runner(
    *,
    model: ModelClient,
    parent_session_id: str,
    agent_registry: AgentRegistry,
    child_tool_factory: ChildToolFactory,
    memory_store: FileMemoryStore | None = None,
    run_id: str = "",
    context_id: str = "",
    compression_config=None,  # CompressionConfig | None
    on_runtime_event: RuntimeEventSink | None = None,
    cancellation_probe: CancellationProbe | None = None,
) -> Callable[..., Awaitable[SubAgentResult]]:
    async def runner(
        *,
        prompt: str,
        agent_type: str,
        max_steps: int | None = None,
        task: dict[str, Any] | None = None,
    ) -> SubAgentResult:
        # Stop starting new sibling SubAgents after cancellation (task 4.5)
        if cancellation_probe is not None and cancellation_probe.is_cancelled():
            from backend.agent.runtime.cancellation import Cancelled
            raise Cancelled("Dispatch cancelled: run is being cancelled")

        try:
            agent_def = agent_registry.resolve(agent_type)
        except Exception as exc:
            return SubAgentResult(
                output=f"Subagent failed: {exc}",
                agent_type=agent_type,
                child_session_id="",
            )

        child_session_id = generate_child_session_id(parent_session_id)
        task_id = ""
        task_type = ""
        if task:
            task_id = task.get("task_id", "")
            task_type = task.get("task_type", "")

        if on_runtime_event:
            on_runtime_event("subagent.started", {
                "task_id": task_id,
                "task_type": task_type,
                "agent_type": agent_def.name,
                "child_session_id": child_session_id,
            })

        factory_result = child_tool_factory(child_session_id, task=task)

        # Handle ChildToolBundle or plain list[Tool]
        if isinstance(factory_result, list):
            all_child_tools = factory_result
        else:
            all_child_tools = factory_result.tools

        child_registry = build_child_tool_registry(all_child_tools)
        child_tools = filter_tools(child_registry, agent_def)
        effective_max_steps = max_steps if max_steps is not None else agent_def.default_max_steps

        try:
            result = await run_subagent(
                model=model,
                agent_def=agent_def,
                prompt=prompt,
                max_steps=effective_max_steps,
                child_session_id=child_session_id,
                child_tools=child_tools,
                memory_store=memory_store,
                run_id=run_id,
                context_id=context_id,
                task_id=task_id,
                on_runtime_event=on_runtime_event,
                cancellation_probe=cancellation_probe,
                compression_config=compression_config,
            )
        except Exception as exc:
            if on_runtime_event:
                on_runtime_event("subagent.completed", {
                    "task_id": task_id,
                    "task_type": task_type,
                    "agent_type": agent_def.name,
                    "child_session_id": child_session_id,
                    "status": "error",
                    "error": str(exc),
                })
            raise

        if on_runtime_event:
            from backend.agent.runtime.review_result import parse_review_result, validate_review_result
            parsed = parse_review_result(result.output)
            validation_errors = validate_review_result(parsed) if parsed else ["Failed to parse JSON"]
            status = "valid" if parsed is not None and not validation_errors else "invalid"
            if result.stopped_by_max_steps:
                status = "max_steps"
            on_runtime_event("subagent.completed", {
                "task_id": task_id,
                "task_type": task_type,
                "agent_type": result.agent_type,
                "child_session_id": result.child_session_id,
                "memory_session_id": result.memory_session_id,
                "status": status,
                "stopped_by_max_steps": result.stopped_by_max_steps,
                "validation_errors": validation_errors if status == "invalid" else [],
            })

        return result

    return runner
