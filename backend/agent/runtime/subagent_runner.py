from __future__ import annotations

import uuid
from typing import Any, Callable, Awaitable, Union

from backend.agent.model.client import ModelClient
from backend.agent.model.messages import Message, Role
from backend.agent.runtime.agent_def import AgentDefinition, AgentRegistry
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


ChildToolFactory = Callable[..., Union[list[Tool], Any]]


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
            append_message(memory_store, subagent_session_id, {
                "role": msg.role.value,
                "content": msg.content if isinstance(msg.content, str) else str(msg.content),
            })

    messages = build_child_messages(agent_def, prompt)
    tool_registry = build_child_tool_registry(child_tools)

    result: AgentResult = await run_loop(
        model=model,
        tool_registry=tool_registry,
        messages=messages,
        max_steps=max_steps,
    )

    # Append result to memory
    if memory_store and subagent_session_id:
        append_message(memory_store, subagent_session_id, {
            "role": "assistant",
            "content": result.output,
        })

    return SubAgentResult(
        output=result.output,
        agent_type=agent_def.name,
        child_session_id=child_session_id,
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
) -> Callable[..., Awaitable[SubAgentResult]]:
    async def runner(
        *,
        prompt: str,
        agent_type: str,
        max_steps: int | None = None,
        task: dict[str, Any] | None = None,
    ) -> SubAgentResult:
        try:
            agent_def = agent_registry.resolve(agent_type)
        except Exception as exc:
            return SubAgentResult(
                output=f"Subagent failed: {exc}",
                agent_type=agent_type,
                child_session_id="",
            )

        child_session_id = generate_child_session_id(parent_session_id)
        factory_result = child_tool_factory(child_session_id, task=task)

        # Handle ChildToolBundle or plain list[Tool]
        if isinstance(factory_result, list):
            all_child_tools = factory_result
        else:
            all_child_tools = factory_result.tools

        child_registry = build_child_tool_registry(all_child_tools)
        child_tools = filter_tools(child_registry, agent_def)
        effective_max_steps = max_steps if max_steps is not None else agent_def.default_max_steps

        # Get task_id if available
        task_id = ""
        if task:
            task_id = task.get("task_id", "")

        return await run_subagent(
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
        )

    return runner
