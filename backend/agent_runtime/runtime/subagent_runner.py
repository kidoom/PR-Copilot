from __future__ import annotations

import uuid
from typing import Any, Callable, Awaitable

from backend.agent_runtime.model.client import ModelClient
from backend.agent_runtime.model.messages import Message, Role
from backend.agent_runtime.runtime.agent_def import AgentDefinition, AgentRegistry
from backend.agent_runtime.runtime.loop import run_loop
from backend.agent_runtime.runtime.results import AgentResult
from backend.agent_runtime.runtime.sub_agent import SubAgentResult
from backend.agent_runtime.tool.protocol import Tool
from backend.agent_runtime.tool.registry import ToolRegistry, filter_tools


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


ChildToolFactory = Callable[[str], list[Tool]]


async def run_subagent(
    *,
    model: ModelClient,
    agent_def: AgentDefinition,
    prompt: str,
    max_steps: int,
    child_session_id: str,
    child_tools: list[Tool],
) -> SubAgentResult:
    messages = build_child_messages(agent_def, prompt)
    tool_registry = build_child_tool_registry(child_tools)

    result: AgentResult = await run_loop(
        model=model,
        tool_registry=tool_registry,
        messages=messages,
        max_steps=max_steps,
    )

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
) -> Callable[[AgentDefinition, str, int], Awaitable[SubAgentResult]]:
    async def runner(
        agent_def: AgentDefinition,
        prompt: str,
        max_steps: int,
    ) -> SubAgentResult:
        child_session_id = generate_child_session_id(parent_session_id)
        child_tools = child_tool_factory(child_session_id)

        return await run_subagent(
            model=model,
            agent_def=agent_def,
            prompt=prompt,
            max_steps=max_steps,
            child_session_id=child_session_id,
            child_tools=child_tools,
        )

    return runner
