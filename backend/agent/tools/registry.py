from __future__ import annotations

from backend.agent.runtime.agent_def import AgentDefinition
from backend.agent.tools.protocol import Tool, ToolSchema, project_schema

TASK_TOOL_NAME = "task"
DENIED_CHILD_TOOL_NAMES = frozenset({"task", "task_tool", "sub_agent"})


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def resolve(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def build_schemas(self) -> list[ToolSchema]:
        return [project_schema(t) for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools.keys())


def filter_tools(
    registry: ToolRegistry,
    agent_def: AgentDefinition,
) -> list[Tool]:
    tools = list(registry._tools.values())

    if agent_def.allowed_tools:
        allowed = set(agent_def.allowed_tools)
        tools = [t for t in tools if t.name in allowed]

    if agent_def.disallowed_tools:
        denied = set(agent_def.disallowed_tools)
        tools = [t for t in tools if t.name not in denied]

    tools = [t for t in tools if t.name not in DENIED_CHILD_TOOL_NAMES]

    return tools
