from __future__ import annotations

import pytest

from backend.agent_runtime.runtime.agent_def import AgentDefinition, AgentRegistry, UnknownAgentError
from backend.agent_runtime.runtime.sub_agent import SubAgentResult
from backend.agent_runtime.runtime.task_tool import TaskTool, TaskToolError, DEFAULT_MAX_STEPS, ABSOLUTE_MAX_STEPS


# --- Fake runner ---


async def fake_runner(agent_def: AgentDefinition, prompt: str, max_steps: int) -> SubAgentResult:
    return SubAgentResult(
        output=f"ran {agent_def.name} with {prompt[:20]} (steps={max_steps})",
        agent_type=agent_def.name,
        stopped_by_max_steps=False,
    )


# --- Setup ---


def _make_registry() -> AgentRegistry:
    reg = AgentRegistry()
    reg.register(AgentDefinition(
        name="reviewer",
        description="Reviews code",
        system_prompt="You review code.",
        default_max_steps=5,
    ))
    reg.register(AgentDefinition(
        name="summarizer",
        description="Summarizes PRs",
        system_prompt="You summarize.",
        default_max_steps=8,
    ))
    return reg


# --- Tests ---


@pytest.mark.asyncio
async def test_valid_delegation():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)
    result = await tool.run(prompt="review this PR", agent_type="reviewer")
    assert result.agent_type == "reviewer"
    assert "reviewer" in result.output


@pytest.mark.asyncio
async def test_valid_delegation_with_task_payload():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)
    result = await tool.run(task={"prompt": "check files"}, agent_type="reviewer")
    assert result.agent_type == "reviewer"


@pytest.mark.asyncio
async def test_valid_delegation_with_task_description():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)
    result = await tool.run(task={"description": "analyze changes"}, agent_type="summarizer")
    assert result.agent_type == "summarizer"


@pytest.mark.asyncio
async def test_unknown_agent_type_raises():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)
    with pytest.raises(UnknownAgentError) as exc_info:
        await tool.run(prompt="test", agent_type="nonexistent")
    assert exc_info.value.name == "nonexistent"
    assert "reviewer" in exc_info.value.available


@pytest.mark.asyncio
async def test_missing_prompt_raises():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)
    with pytest.raises(TaskToolError) as exc_info:
        await tool.run(prompt=None, agent_type="reviewer")
    assert "non-empty prompt" in str(exc_info.value)


@pytest.mark.asyncio
async def test_empty_prompt_raises():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)
    with pytest.raises(TaskToolError):
        await tool.run(prompt="   ", agent_type="reviewer")


@pytest.mark.asyncio
async def test_missing_task_payload_raises():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)
    with pytest.raises(TaskToolError):
        await tool.run(task={}, agent_type="reviewer")


@pytest.mark.asyncio
async def test_uses_default_max_steps():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)
    result = await tool.run(prompt="test", agent_type="reviewer")
    assert "steps=5" in result.output


@pytest.mark.asyncio
async def test_explicit_max_steps_overrides():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)
    result = await tool.run(prompt="test", agent_type="reviewer", max_steps=3)
    assert "steps=3" in result.output


@pytest.mark.asyncio
async def test_max_steps_clamped_to_1():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)
    result = await tool.run(prompt="test", agent_type="reviewer", max_steps=0)
    assert "steps=1" in result.output


@pytest.mark.asyncio
async def test_max_steps_clamped_to_absolute():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)
    result = await tool.run(prompt="test", agent_type="reviewer", max_steps=999)
    assert f"steps={ABSOLUTE_MAX_STEPS}" in result.output
