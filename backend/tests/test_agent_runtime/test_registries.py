from __future__ import annotations

from typing import Any

from backend.agent_runtime.tool import RiskLevel, Tool, ToolSchema, project_schema
from backend.agent_runtime.registry import ToolRegistry, filter_tools
from backend.agent_runtime.agent_def import AgentDefinition, AgentRegistry, UnknownAgentError


# --- Fake tool for tests ---


class FakeSearchTool(Tool):
    @property
    def name(self) -> str:
        return "search"

    @property
    def description(self) -> str:
        return "Search for files"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"query": {"type": "string"}}}

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.LOW

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def is_concurrency_safe(self) -> bool:
        return True

    async def call(self, input: dict[str, Any]) -> str:
        return f"results for {input.get('query', '')}"


class FakeWriteTool(Tool):
    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Write to a file"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"path": {"type": "string"}}}

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.HIGH

    @property
    def is_read_only(self) -> bool:
        return False

    @property
    def is_concurrency_safe(self) -> bool:
        return False

    async def call(self, input: dict[str, Any]) -> str:
        return "written"


class FakeTaskTool(Tool):
    @property
    def name(self) -> str:
        return "task"

    @property
    def description(self) -> str:
        return "Delegate to sub-agent"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"prompt": {"type": "string"}}}

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.LOW

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def is_concurrency_safe(self) -> bool:
        return True

    async def call(self, input: dict[str, Any]) -> str:
        return "delegated"


# --- Tool schema projection tests ---


def test_project_schema_excludes_risk_level():
    tool = FakeSearchTool()
    schema = project_schema(tool)
    assert isinstance(schema, ToolSchema)
    assert schema.name == "search"
    assert schema.description == "Search for files"
    assert "query" in schema.input_schema.get("properties", {})
    assert not hasattr(schema, "risk_level")


def test_project_schema_only_includes_model_safe_fields():
    tool = FakeWriteTool()
    schema = project_schema(tool)
    assert schema.name == "write_file"
    assert schema.description == "Write to a file"
    assert schema.input_schema == tool.input_schema


# --- Tool registry tests ---


def test_register_and_resolve():
    reg = ToolRegistry()
    tool = FakeSearchTool()
    reg.register(tool)
    assert reg.resolve("search") is tool


def test_resolve_unknown_returns_none():
    reg = ToolRegistry()
    assert reg.resolve("nonexistent") is None


def test_build_schemas():
    reg = ToolRegistry()
    reg.register(FakeSearchTool())
    reg.register(FakeWriteTool())
    schemas = reg.build_schemas()
    assert len(schemas) == 2
    names = {s.name for s in schemas}
    assert names == {"search", "write_file"}


def test_names():
    reg = ToolRegistry()
    reg.register(FakeSearchTool())
    assert reg.names() == ["search"]


# --- Agent registry tests ---


def test_agent_register_and_resolve():
    reg = AgentRegistry()
    agent = AgentDefinition(
        name="reviewer",
        description="Code reviewer",
        system_prompt="You review code.",
    )
    reg.register(agent)
    assert reg.resolve("reviewer") is agent


def test_agent_resolve_unknown_raises():
    reg = AgentRegistry()
    try:
        reg.resolve("nonexistent")
        assert False, "Should have raised"
    except UnknownAgentError as e:
        assert e.name == "nonexistent"
        assert e.available == []


def test_agent_resolve_unknown_lists_available():
    reg = AgentRegistry()
    reg.register(AgentDefinition(name="a", description="d", system_prompt="p"))
    reg.register(AgentDefinition(name="b", description="d", system_prompt="p"))
    try:
        reg.resolve("c")
        assert False, "Should have raised"
    except UnknownAgentError as e:
        assert set(e.available) == {"a", "b"}


def test_agent_names():
    reg = AgentRegistry()
    reg.register(AgentDefinition(name="x", description="d", system_prompt="p"))
    assert reg.names() == ["x"]


# --- Tool filtering tests ---


def _make_reg_with_all_tools() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(FakeSearchTool())
    reg.register(FakeWriteTool())
    reg.register(FakeTaskTool())
    return reg


def test_filter_tools_removes_task_tool():
    reg = _make_reg_with_all_tools()
    agent = AgentDefinition(name="a", description="d", system_prompt="p")
    filtered = filter_tools(reg, agent)
    names = {t.name for t in filtered}
    assert "task" not in names


def test_filter_tools_allowed_restricts():
    reg = _make_reg_with_all_tools()
    agent = AgentDefinition(
        name="a", description="d", system_prompt="p", allowed_tools=["search"]
    )
    filtered = filter_tools(reg, agent)
    names = {t.name for t in filtered}
    assert names == {"search"}


def test_filter_tools_denied_removes():
    reg = _make_reg_with_all_tools()
    agent = AgentDefinition(
        name="a", description="d", system_prompt="p", disallowed_tools=["write_file"]
    )
    filtered = filter_tools(reg, agent)
    names = {t.name for t in filtered}
    assert "write_file" not in names
    assert "search" in names


def test_filter_tools_allowed_and_denied():
    reg = _make_reg_with_all_tools()
    agent = AgentDefinition(
        name="a",
        description="d",
        system_prompt="p",
        allowed_tools=["search", "write_file"],
        disallowed_tools=["write_file"],
    )
    filtered = filter_tools(reg, agent)
    names = {t.name for t in filtered}
    assert names == {"search"}


def test_filter_tools_empty_allowed_gives_all_except_task():
    reg = _make_reg_with_all_tools()
    agent = AgentDefinition(name="a", description="d", system_prompt="p")
    filtered = filter_tools(reg, agent)
    names = {t.name for t in filtered}
    assert "task" not in names
    assert "search" in names
    assert "write_file" in names
