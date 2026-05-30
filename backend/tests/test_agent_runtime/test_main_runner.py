from __future__ import annotations

import json
import tempfile
from typing import Any

import pytest

from backend.agent.model.client import ModelClient
from backend.agent.model.messages import Message, ModelResponse, Role, ToolUseBlock
from backend.agent.runtime.events import RUN_STARTED, RUN_COMPLETED, RUN_FAILED
from backend.agent.runtime.main_runner import run_main_agent
from backend.agent.runtime.memory.store import FileMemoryStore
from backend.agent.runtime.run_manager import RunManager
from backend.agent.tools.protocol import RiskLevel, Tool, ToolSchema
from backend.deps import AgentDeps, create_agent_deps


class FakeModel(ModelClient):
    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self._call_count = 0
        self.captured_messages: list[list[Message]] = []

    async def chat(self, messages: list[Message], tool_schemas: list[ToolSchema] | None = None) -> ModelResponse:
        self.captured_messages.append(list(messages))
        resp = self._responses[self._call_count]
        self._call_count += 1
        return resp


def _make_deps_with_fake_model(model: ModelClient, tmp_path: str | None = None) -> AgentDeps:
    import os
    os.environ.setdefault("OPENAI_API_KEY", "test-key")
    deps = create_agent_deps()

    # Use temporary directory for memory store
    if tmp_path is None:
        tmp_path = tempfile.mkdtemp()
    deps.memory_store = FileMemoryStore(tmp_path)

    original_new_model = deps.new_model

    def _new_model():
        return model

    deps.new_model = _new_model
    return deps


@pytest.fixture
def tmp_dir():
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.mark.asyncio
async def test_run_main_agent_publishes_started_and_completed_events(tmp_dir):
    model = FakeModel([ModelResponse(content="done", tool_use_blocks=[])])
    deps = _make_deps_with_fake_model(model, tmp_dir)
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")

    captured_events: list[str] = []

    def sink(event):
        captured_events.append(event.type)

    result = await run_main_agent(
        run_id="run-1",
        context_id="ctx-1",
        task_plan={"context_id": "ctx-1", "tasks": [], "routes": []},
        pr_context=None,
        repo_root="/repo",
        deps=deps,
        run_manager=mgr,
        event_sink=sink,
    )

    assert "error" not in result
    assert result["output"] == "done"
    assert mgr.get_run("run-1").status.value == "completed"
    assert RUN_STARTED in captured_events

    retained = mgr.get_retained_events("run-1")
    event_types = [e.type for e in retained]
    assert RUN_STARTED in event_types
    assert RUN_COMPLETED in event_types


@pytest.mark.asyncio
async def test_run_main_agent_task_plan_reaches_messages(tmp_dir):
    model = FakeModel([ModelResponse(content="ok", tool_use_blocks=[])])
    deps = _make_deps_with_fake_model(model, tmp_dir)
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")

    task_plan = {
        "context_id": "ctx-1",
        "tasks": [{"task_id": "t1", "task_type": "security_context"}],
        "routes": [],
    }

    await run_main_agent(
        run_id="run-1",
        context_id="ctx-1",
        task_plan=task_plan,
        pr_context=None,
        repo_root="/repo",
        deps=deps,
        run_manager=mgr,
    )

    user_messages = [
        m for m in model.captured_messages[0]
        if m.role == Role.USER
    ]
    assert len(user_messages) >= 1
    payload = json.loads(user_messages[0].content)
    assert payload["task_plan"]["tasks"][0]["task_id"] == "t1"


@pytest.mark.asyncio
async def test_run_main_agent_failure_publishes_error(tmp_dir):
    class BrokenModel(ModelClient):
        async def chat(self, messages, tool_schemas=None):
            raise RuntimeError("model exploded")

    deps = _make_deps_with_fake_model(BrokenModel(), tmp_dir)
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")

    result = await run_main_agent(
        run_id="run-1",
        context_id="ctx-1",
        task_plan={"context_id": "ctx-1", "tasks": [], "routes": []},
        pr_context=None,
        repo_root="/repo",
        deps=deps,
        run_manager=mgr,
    )

    assert "error" in result
    assert "model exploded" in result["error"]
    assert mgr.get_run("run-1").status.value == "failed"

    retained = mgr.get_retained_events("run-1")
    event_types = [e.type for e in retained]
    assert RUN_FAILED in event_types


@pytest.mark.asyncio
async def test_run_main_agent_passes_max_steps(tmp_dir):
    model = FakeModel([ModelResponse(content="ok", tool_use_blocks=[])])
    deps = _make_deps_with_fake_model(model, tmp_dir)
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")

    result = await run_main_agent(
        run_id="run-1",
        context_id="ctx-1",
        task_plan={"context_id": "ctx-1", "tasks": [], "routes": []},
        pr_context=None,
        repo_root="/repo",
        deps=deps,
        run_manager=mgr,
        max_steps=5,
    )

    assert result["output"] == "ok"
