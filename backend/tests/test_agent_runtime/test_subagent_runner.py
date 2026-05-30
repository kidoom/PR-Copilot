from __future__ import annotations

import json
import pytest
from typing import Any

from backend.agent.model.messages import Message, ModelResponse, Role, TokenUsage, ToolUseBlock
from backend.agent.model.client import ModelClient
from backend.agent.runtime.agent_def import AgentDefinition, AgentRegistry, UnknownAgentError
from backend.agent.runtime.loop import run_loop
from backend.agent.runtime.results import AgentResult
from backend.agent.runtime.sub_agent import SubAgentResult
from backend.agent.runtime.subagent_runner import (
    build_child_messages,
    build_child_tool_registry,
    build_subagent_runner,
    generate_child_session_id,
    run_subagent,
)
from backend.agent.tools.task import TaskTool, TaskToolError, ABSOLUTE_MAX_STEPS
from backend.agent.tools.protocol import RiskLevel, Tool, ToolSchema
from backend.agent.tools.registry import ToolRegistry, DENIED_CHILD_TOOL_NAMES


# --- Fakes ---


class FakeModelClient(ModelClient):
    def __init__(self, responses: list[ModelResponse] | None = None) -> None:
        self._responses = responses or []
        self._call_index = 0
        self.messages_log: list[list[Message]] = []

    async def chat(self, messages: list[Message], tool_schemas: list[ToolSchema] | None = None) -> ModelResponse:
        self.messages_log.append(list(messages))
        if self._call_index < len(self._responses):
            resp = self._responses[self._call_index]
            self._call_index += 1
            return resp
        return ModelResponse(content="done", tool_use_blocks=[], token_usage=TokenUsage(5, 10))


class FakeTool(Tool):
    def __init__(self, name: str, read_only: bool = True) -> None:
        self._name = name
        self._read_only = read_only

    @property
    def name(self) -> str: return self._name
    @property
    def description(self) -> str: return f"Tool {self._name}"
    @property
    def input_schema(self) -> dict[str, Any]: return {"type": "object", "properties": {}}
    @property
    def risk_level(self) -> RiskLevel: return RiskLevel.LOW
    @property
    def is_read_only(self) -> bool: return self._read_only
    @property
    def is_concurrency_safe(self) -> bool: return True

    async def call(self, input: dict[str, Any]) -> str:
        return f"called {self._name}"


async def fake_runner(*, prompt: str, agent_type: str, max_steps: int | None = None) -> SubAgentResult:
    return SubAgentResult(
        output=f"ran {agent_type} with {prompt} (steps={max_steps})",
        agent_type=agent_type,
        stopped_by_max_steps=False,
    )


def _make_registry() -> AgentRegistry:
    reg = AgentRegistry()
    reg.register(AgentDefinition(
        name="reviewer", description="Reviews code",
        system_prompt="You review code.", default_max_steps=5,
    ))
    reg.register(AgentDefinition(
        name="summarizer", description="Summarizes PRs",
        system_prompt="You summarize.", default_max_steps=8,
    ))
    return reg


# --- 4.1 TaskTool.call() tests ---


@pytest.mark.asyncio
async def test_call_delegates_to_run():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)
    result_json = await tool.call({"prompt": "review this", "agent_type": "reviewer"})
    result = json.loads(result_json)
    assert result["agent_type"] == "reviewer"
    assert "reviewer" in result["output"]


@pytest.mark.asyncio
async def test_call_returns_error_for_empty_prompt():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)
    result_json = await tool.call({"prompt": "  ", "agent_type": "reviewer"})
    result = json.loads(result_json)
    assert "error" in result


@pytest.mark.asyncio
async def test_call_returns_error_for_unknown_agent():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)
    result_json = await tool.call({"prompt": "test", "agent_type": "nonexistent"})
    result = json.loads(result_json)
    assert "error" in result
    assert "Available agent types" in result["error"]


@pytest.mark.asyncio
async def test_call_with_task_payload():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)
    result_json = await tool.call({"task": {"queries": ["q1", "q2"]}, "agent_type": "reviewer"})
    result = json.loads(result_json)
    assert "q1" in result["output"]


def test_task_tool_properties():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)
    assert tool.name == "task"
    assert tool.risk_level == RiskLevel.MEDIUM
    assert tool.is_read_only is False
    assert tool.is_concurrency_safe is False
    assert "type" in tool.input_schema


# --- 4.2 run_subagent tests ---


@pytest.mark.asyncio
async def test_run_subagent_reuses_run_loop():
    model = FakeModelClient([
        ModelResponse(content="final answer", tool_use_blocks=[], token_usage=TokenUsage(10, 20)),
    ])
    agent_def = AgentDefinition(
        name="test-agent", description="Test",
        system_prompt="You are a test agent.", default_max_steps=5,
    )
    tools = [FakeTool("read_file")]

    result = await run_subagent(
        model=model,
        agent_def=agent_def,
        prompt="do something",
        max_steps=5,
        child_session_id="child_1",
        child_tools=tools,
    )

    assert result.output == "final answer"
    assert result.agent_type == "test-agent"
    assert result.child_session_id == "child_1"
    assert result.stopped_by_max_steps is False

    assert len(model.messages_log) == 1
    child_messages = model.messages_log[0]
    assert child_messages[0].role == Role.SYSTEM
    assert child_messages[0].content == "You are a test agent."
    assert child_messages[1].role == Role.USER
    assert child_messages[1].content == "do something"


@pytest.mark.asyncio
async def test_run_subagent_returns_max_steps_status():
    class AlwaysToolModel(ModelClient):
        def __init__(self) -> None:
            self.call_count = 0
        async def chat(self, messages: list[Message], tool_schemas: list[ToolSchema] | None = None) -> ModelResponse:
            self.call_count += 1
            return ModelResponse(
                content="thinking",
                tool_use_blocks=[ToolUseBlock(
                    tool_use_id=f"t_{self.call_count}",
                    name="nonexistent_tool",
                    input={},
                )],
                token_usage=TokenUsage(1, 1),
            )

    model = AlwaysToolModel()
    agent_def = AgentDefinition(
        name="test-agent", description="Test",
        system_prompt="sys", default_max_steps=2,
    )
    result = await run_subagent(
        model=model,
        agent_def=agent_def,
        prompt="run forever",
        max_steps=2,
        child_session_id="child_2",
        child_tools=[],
    )
    assert result.stopped_by_max_steps is True
    assert model.call_count == 2


@pytest.mark.asyncio
async def test_generate_child_session_id_unique():
    ids = {generate_child_session_id("parent") for _ in range(100)}
    assert len(ids) == 100
    for cid in ids:
        assert cid.startswith("parent.child_")


def test_build_child_messages():
    agent_def = AgentDefinition(
        name="a", description="d", system_prompt="sys prompt",
    )
    msgs = build_child_messages(agent_def, "user prompt")
    assert len(msgs) == 2
    assert msgs[0].role == Role.SYSTEM
    assert msgs[0].content == "sys prompt"
    assert msgs[1].role == Role.USER
    assert msgs[1].content == "user prompt"


def test_build_child_messages_no_system():
    agent_def = AgentDefinition(name="a", description="d", system_prompt="")
    msgs = build_child_messages(agent_def, "user prompt")
    assert len(msgs) == 1
    assert msgs[0].role == Role.USER


# --- 4.3 Child todo_write isolation ---


@pytest.mark.asyncio
async def test_child_todo_write_does_not_mutate_parent():
    from backend.agent.tools.repo_context.models import RepoContextSession, RepoVerificationState, VerificationStatus, TaskBudget

    parent_session = RepoContextSession(
        context_id="ctx_parent", task_id="task_parent",
        repo_root=".",
    )
    parent_session.todos = [{"content": "parent task", "status": "in_progress"}]

    child_session_id = generate_child_session_id("ctx_parent")
    child_session = RepoContextSession(
        context_id=child_session_id, task_id="task_child",
        repo_root=".",
    )
    child_session.todos = [{"content": "child task", "status": "in_progress"}]

    assert parent_session.todos[0]["content"] == "parent task"
    assert child_session.todos[0]["content"] == "child task"

    child_session.todos.append({"content": "child task 2", "status": "pending"})
    assert len(parent_session.todos) == 1
    assert len(child_session.todos) == 2


# --- 4.4 Sibling subagent independence ---


@pytest.mark.asyncio
async def test_sibling_subagents_independent_sessions():
    parent_id = "ctx_parent"
    child1_id = generate_child_session_id(parent_id)
    child2_id = generate_child_session_id(parent_id)

    assert child1_id != child2_id
    assert child1_id.startswith(parent_id)
    assert child2_id.startswith(parent_id)


@pytest.mark.asyncio
async def test_sibling_subagents_independent_results():
    model = FakeModelClient([
        ModelResponse(content="result A", tool_use_blocks=[], token_usage=TokenUsage(5, 10)),
        ModelResponse(content="result B", tool_use_blocks=[], token_usage=TokenUsage(3, 7)),
    ])
    agent_def = AgentDefinition(
        name="worker", description="Works",
        system_prompt="sys", default_max_steps=5,
    )

    result_a = await run_subagent(
        model=model, agent_def=agent_def, prompt="task A",
        max_steps=5, child_session_id="child_a", child_tools=[],
    )
    result_b = await run_subagent(
        model=model, agent_def=agent_def, prompt="task B",
        max_steps=5, child_session_id="child_b", child_tools=[],
    )

    assert result_a.output == "result A"
    assert result_b.output == "result B"
    assert result_a.child_session_id == "child_a"
    assert result_b.child_session_id == "child_b"


# --- 4.5 Child tool filtering ---


def test_child_tools_filtered_by_allowlist():
    agent_def = AgentDefinition(
        name="restricted", description="Restricted agent",
        system_prompt="sys",
        allowed_tools=["todo_write", "read_file_patch"],
    )
    tools = [
        FakeTool("todo_write"),
        FakeTool("read_file_patch"),
        FakeTool("search_repo"),
        FakeTool("finish_context_package"),
    ]
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)

    from backend.agent.tools.registry import filter_tools
    filtered = filter_tools(registry, agent_def)
    names = {t.name for t in filtered}
    assert "todo_write" in names
    assert "read_file_patch" in names
    assert "search_repo" not in names
    assert "finish_context_package" not in names


def test_recursive_delegation_tools_denied():
    agent_def = AgentDefinition(
        name="sneaky", description="Tries to recurse",
        system_prompt="sys",
        allowed_tools=["todo_write", "task", "task_tool", "sub_agent", "read_file"],
    )
    tools = [
        FakeTool("todo_write"),
        FakeTool("task"),
        FakeTool("task_tool"),
        FakeTool("sub_agent"),
        FakeTool("read_file"),
    ]
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)

    from backend.agent.tools.registry import filter_tools
    filtered = filter_tools(registry, agent_def)
    names = {t.name for t in filtered}
    for denied in DENIED_CHILD_TOOL_NAMES:
        assert denied not in names
    assert "todo_write" in names
    assert "read_file" in names


def test_disallowed_tools_removed():
    agent_def = AgentDefinition(
        name="agent", description="d", system_prompt="sys",
        allowed_tools=["todo_write", "read_file", "search_repo"],
        disallowed_tools=["search_repo"],
    )
    tools = [FakeTool("todo_write"), FakeTool("read_file"), FakeTool("search_repo")]
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)

    from backend.agent.tools.registry import filter_tools
    filtered = filter_tools(registry, agent_def)
    names = {t.name for t in filtered}
    assert "todo_write" in names
    assert "read_file" in names
    assert "search_repo" not in names


def test_build_child_tool_registry():
    tools = [FakeTool("a"), FakeTool("b")]
    registry = build_child_tool_registry(tools)
    assert registry.resolve("a") is not None
    assert registry.resolve("b") is not None
    assert registry.resolve("c") is None


# --- build_subagent_runner integration ---


@pytest.mark.asyncio
async def test_build_subagent_runner_integration():
    model = FakeModelClient([
        ModelResponse(content="child done", tool_use_blocks=[], token_usage=TokenUsage(10, 20)),
    ])
    reg = _make_registry()

    def tool_factory(child_session_id: str) -> list[Tool]:
        return [FakeTool("todo_write"), FakeTool("read_file")]

    runner = build_subagent_runner(
        model=model,
        parent_session_id="parent_ctx",
        agent_registry=reg,
        child_tool_factory=tool_factory,
    )

    result = await runner(prompt="review code", agent_type="reviewer", max_steps=5)

    assert result.output == "child done"
    assert result.agent_type == "reviewer"
    assert result.child_session_id.startswith("parent_ctx.child_")
    assert result.stopped_by_max_steps is False


@pytest.mark.asyncio
async def test_runner_creates_fresh_messages_each_call():
    model = FakeModelClient([
        ModelResponse(content="r1", tool_use_blocks=[], token_usage=TokenUsage(1, 1)),
        ModelResponse(content="r2", tool_use_blocks=[], token_usage=TokenUsage(1, 1)),
    ])
    reg = _make_registry()

    def tool_factory(cid: str) -> list[Tool]:
        return []

    runner = build_subagent_runner(
        model=model, parent_session_id="p",
        agent_registry=reg, child_tool_factory=tool_factory,
    )
    await runner(prompt="first", agent_type="reviewer", max_steps=3)
    await runner(prompt="second", agent_type="reviewer", max_steps=3)

    assert len(model.messages_log) == 2
    assert model.messages_log[0][1].content == "first"
    assert model.messages_log[1][1].content == "second"
