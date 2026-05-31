from __future__ import annotations

import json
import asyncio
import pytest

from backend.agent.runtime.agent_def import AgentDefinition, AgentRegistry
from backend.agent.runtime.sub_agent import SubAgentResult
from backend.agent.tools.task import TaskTool, TaskToolError, DEFAULT_MAX_STEPS, ABSOLUTE_MAX_STEPS


def _make_valid_review_output(agent_type: str, prompt: str) -> str:
    """Create a valid structured review result JSON."""
    return json.dumps({
        "status": "success",
        "summary": f"ran {agent_type} with {prompt}",
        "findings": [],
        "uncertainties": [],
        "notes": [],
    })


async def fake_runner(*, prompt: str, agent_type: str, max_steps: int | None = None, task: dict | None = None) -> SubAgentResult:
    return SubAgentResult(
        output=_make_valid_review_output(agent_type, prompt),
        agent_type=agent_type,
        stopped_by_max_steps=False,
    )


async def fake_runner_unsupported_finding(*, prompt: str, agent_type: str, max_steps: int | None = None, task: dict | None = None) -> SubAgentResult:
    return SubAgentResult(
        output=json.dumps({
            "status": "success",
            "summary": "unsupported claim",
            "findings": [{
                "claim": "This claim has no evidence",
                "confidence": 0.8,
                "severity": "medium",
                "evidence": [],
            }],
            "uncertainties": [],
            "notes": [],
        }),
        agent_type=agent_type,
        stopped_by_max_steps=False,
    )


def _make_registry() -> AgentRegistry:
    reg = AgentRegistry()
    reg.register(AgentDefinition(name="reviewer", description="Reviews code", system_prompt="You review code.", default_max_steps=5))
    reg.register(AgentDefinition(name="summarizer", description="Summarizes PRs", system_prompt="You summarize.", default_max_steps=8))
    reg.register(AgentDefinition(name="security-context-agent", description="Checks security context", system_prompt="You check security.", default_max_steps=5))
    reg.register(AgentDefinition(name="test-context-agent", description="Finds tests", system_prompt="You find tests.", default_max_steps=5))
    return reg


@pytest.mark.asyncio
async def test_valid_delegation():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)
    result = await tool.run(prompt="review this PR", agent_type="reviewer")
    assert result.agent_type == "reviewer"
    # Output is now JSON
    output = json.loads(result.output)
    assert "reviewer" in output["summary"]


@pytest.mark.asyncio
async def test_valid_delegation_with_task_payload():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)
    result = await tool.run(task={"prompt": "check files"}, agent_type="reviewer")
    assert result.agent_type == "reviewer"


@pytest.mark.asyncio
async def test_unknown_agent_type_raises():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)
    with pytest.raises(TaskToolError):
        await tool.run(prompt="test", agent_type="nonexistent")


@pytest.mark.asyncio
async def test_missing_prompt_raises():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)
    with pytest.raises(TaskToolError):
        await tool.run(prompt=None, agent_type="reviewer")


@pytest.mark.asyncio
async def test_empty_prompt_raises():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)
    with pytest.raises(TaskToolError):
        await tool.run(prompt="   ", agent_type="reviewer")


@pytest.mark.asyncio
async def test_uses_default_max_steps():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)
    result = await tool.run(prompt="test", agent_type="reviewer")
    # Output is now JSON, check summary contains expected info
    output = json.loads(result.output)
    assert "reviewer" in output["summary"]


@pytest.mark.asyncio
async def test_explicit_max_steps_overrides():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)
    result = await tool.run(prompt="test", agent_type="reviewer", max_steps=3)
    # Output is now JSON
    output = json.loads(result.output)
    assert "reviewer" in output["summary"]


@pytest.mark.asyncio
async def test_max_steps_clamped_to_1():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)
    result = await tool.run(prompt="test", agent_type="reviewer", max_steps=0)
    # Output is now JSON
    output = json.loads(result.output)
    assert "reviewer" in output["summary"]


@pytest.mark.asyncio
async def test_max_steps_clamped_to_absolute():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)
    result = await tool.run(prompt="test", agent_type="reviewer", max_steps=999)
    # Output is now JSON
    output = json.loads(result.output)
    assert "reviewer" in output["summary"]


@pytest.mark.asyncio
async def test_task_with_intent_field():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)
    result = await tool.run(task={"task_type": "security_context", "intent": "Check for SQL injection risks"}, agent_type="reviewer")
    assert "Check for SQL injection" in result.output


@pytest.mark.asyncio
async def test_task_with_queries_field():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)
    result = await tool.run(task={"task_type": "security_context", "queries": ["scan for secrets", "check auth patterns"]}, agent_type="reviewer")
    assert "scan for secrets" in result.output
    assert "check auth patterns" in result.output


@pytest.mark.asyncio
async def test_task_with_task_type_and_target_files():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)
    result = await tool.run(task={"task_type": "test_context", "target_files": ["src/auth.py", "src/login.py"]}, agent_type="reviewer")
    assert "test_context" in result.output
    assert "src/auth.py" in result.output


@pytest.mark.asyncio
async def test_intent_takes_priority_over_queries():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)
    result = await tool.run(task={"intent": "Analyze security", "queries": ["q1", "q2"]}, agent_type="reviewer")
    assert "Analyze security" in result.output


@pytest.mark.asyncio
async def test_run_many_dispatches_tasks_by_routes():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)

    results = await tool.run_many(
        tasks=[
            {
                "task_id": "task_sec",
                "task_type": "security_context",
                "route_key": "route:security_context",
                "intent": "check auth",
                "target": {"files": ["backend/api/routes/review.py"]},
                "queries": ["auth"],
            },
            {
                "task_id": "task_test",
                "task_type": "test_context",
                "route_key": "route:test_context",
                "intent": "find related tests",
                "target": {"files": ["backend/domain/review/intake.py"]},
                "queries": ["intake"],
            },
        ],
        routes=[
            {"task_type": "security_context", "route_key": "route:security_context", "agent_type": "security-context-agent", "max_steps": 4},
            {"task_type": "test_context", "route_key": "route:test_context", "agent_type": "test-context-agent", "max_steps": 3},
        ],
    )

    assert [r["agent_type"] for r in results] == ["security-context-agent", "test-context-agent"]
    assert all(r["status"] == "ok" for r in results)
    # Output is now JSON
    output = json.loads(results[0]["output"])
    assert "security-context-agent" in output["summary"]
    output2 = json.loads(results[1]["output"])
    assert "test-context-agent" in output2["summary"]


@pytest.mark.asyncio
async def test_call_dispatches_task_plan_payload():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)

    result_json = await tool.call({
        "task_plan": {
            "tasks": [
                {
                    "task_id": "task_sec",
                    "task_type": "security_context",
                    "route_key": "route:security_context",
                    "intent": "check auth",
                    "queries": ["auth"],
                },
            ],
            "routes": [
                {"task_type": "security_context", "route_key": "route:security_context", "agent_type": "security-context-agent", "max_steps": 4},
            ],
        },
    })

    result = __import__("json").loads(result_json)
    assert result["dispatched"] == 1
    assert result["results"][0]["agent_type"] == "security-context-agent"


@pytest.mark.asyncio
async def test_run_many_requires_non_empty_tasks():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner)

    with pytest.raises(TaskToolError):
        await tool.run_many(tasks=[], routes=[])


@pytest.mark.asyncio
async def test_run_many_injects_context_id_from_task_plan():
    captured_tasks: list[dict | None] = []

    async def capturing_runner(*, prompt: str, agent_type: str, max_steps: int | None = None, task: dict | None = None) -> SubAgentResult:
        captured_tasks.append(task)
        return SubAgentResult(output="done", agent_type=agent_type, stopped_by_max_steps=False)

    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=capturing_runner)

    await tool.run_many(
        task_plan={
            "context_id": "ctx_abc",
            "tasks": [{"task_id": "t1", "task_type": "security_context"}],
            "routes": [{"task_type": "security_context", "agent_type": "security-context-agent"}],
        },
    )

    assert len(captured_tasks) == 1
    assert captured_tasks[0]["context_id"] == "ctx_abc"


@pytest.mark.asyncio
async def test_run_many_does_not_override_existing_context_id():
    captured_tasks: list[dict | None] = []

    async def capturing_runner(*, prompt: str, agent_type: str, max_steps: int | None = None, task: dict | None = None) -> SubAgentResult:
        captured_tasks.append(task)
        return SubAgentResult(output="done", agent_type=agent_type, stopped_by_max_steps=False)

    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=capturing_runner)

    await tool.run_many(
        task_plan={
            "context_id": "ctx_from_plan",
            "tasks": [{"task_id": "t1", "task_type": "security_context", "context_id": "ctx_from_task"}],
            "routes": [{"task_type": "security_context", "agent_type": "security-context-agent"}],
        },
    )

    assert captured_tasks[0]["context_id"] == "ctx_from_task"


@pytest.mark.asyncio
async def test_run_many_continues_on_runner_exception():
    call_count = 0

    async def failing_runner(*, prompt: str, agent_type: str, max_steps: int | None = None, task: dict | None = None) -> SubAgentResult:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("model crashed")
        return SubAgentResult(
            output=_make_valid_review_output(agent_type, prompt),
            agent_type=agent_type,
            stopped_by_max_steps=False,
        )

    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=failing_runner)

    results = await tool.run_many(
        tasks=[
            {"task_id": "t1", "task_type": "security_context"},
            {"task_id": "t2", "task_type": "test_context"},
        ],
        routes=[
            {"task_type": "security_context", "agent_type": "security-context-agent"},
            {"task_type": "test_context", "agent_type": "test-context-agent"},
        ],
    )

    assert len(results) == 2
    assert results[0]["status"] == "error"
    assert "model crashed" in results[0]["error"]
    assert results[1]["status"] == "ok"


@pytest.mark.asyncio
async def test_run_many_preserves_input_order_when_tasks_complete_out_of_order():
    async def delayed_runner(*, prompt: str, agent_type: str, max_steps: int | None = None, task: dict | None = None) -> SubAgentResult:
        delay = 0.02 if task and task.get("task_id") == "slow" else 0.001
        await asyncio.sleep(delay)
        return SubAgentResult(
            output=_make_valid_review_output(agent_type, prompt),
            agent_type=agent_type,
            stopped_by_max_steps=False,
        )

    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=delayed_runner, max_concurrent_tasks=2)

    results = await tool.run_many(
        tasks=[
            {"task_id": "slow", "task_type": "security_context"},
            {"task_id": "fast", "task_type": "test_context"},
        ],
        routes=[
            {"task_type": "security_context", "agent_type": "security-context-agent"},
            {"task_type": "test_context", "agent_type": "test-context-agent"},
        ],
    )

    assert [r["task_id"] for r in results] == ["slow", "fast"]
    assert [r["index"] for r in results] == [0, 1]


@pytest.mark.asyncio
async def test_run_many_respects_concurrency_limit():
    active = 0
    max_seen = 0

    async def tracking_runner(*, prompt: str, agent_type: str, max_steps: int | None = None, task: dict | None = None) -> SubAgentResult:
        nonlocal active, max_seen
        active += 1
        max_seen = max(max_seen, active)
        await asyncio.sleep(0.01)
        active -= 1
        return SubAgentResult(
            output=_make_valid_review_output(agent_type, prompt),
            agent_type=agent_type,
            stopped_by_max_steps=False,
        )

    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=tracking_runner, max_concurrent_tasks=2)

    await tool.run_many(
        tasks=[
            {"task_id": f"t{i}", "task_type": "security_context"}
            for i in range(5)
        ],
        routes=[{"task_type": "security_context", "agent_type": "security-context-agent"}],
    )

    assert max_seen == 2


@pytest.mark.asyncio
async def test_call_marks_finding_without_evidence_invalid():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner_unsupported_finding)

    result_json = await tool.call({"prompt": "review this", "agent_type": "reviewer"})
    result = json.loads(result_json)

    assert result["parse_status"] == "invalid"
    assert any("evidence is required" in e for e in result["validation_errors"])


@pytest.mark.asyncio
async def test_run_many_marks_finding_without_evidence_invalid():
    reg = _make_registry()
    tool = TaskTool(agent_registry=reg, runner=fake_runner_unsupported_finding)

    results = await tool.run_many(
        tasks=[{"task_id": "t1", "task_type": "security_context"}],
        routes=[{"task_type": "security_context", "agent_type": "security-context-agent"}],
    )

    assert results[0]["status"] == "invalid"
    assert any("evidence is required" in e for e in results[0]["validation_errors"])
