from __future__ import annotations

import asyncio
import json
import tempfile
import time
from typing import Any

import pytest

from backend.agent.model.client import ModelClient
from backend.agent.model.messages import Message, ModelResponse, Role
from backend.agent.runtime.events import RUN_STARTED, RUN_COMPLETED, RUN_FAILED, MESSAGE_DELTA, TOOL_CALL, TOOL_RESULT, SUBAGENT_STARTED, SUBAGENT_COMPLETED
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
    assert "raw_output" not in result  # raw_output excluded from frontend payload
    assert result["status"] == "completed"
    assert mgr.get_run("run-1").status.value == "completed"
    assert RUN_STARTED in captured_events

    retained = mgr.get_retained_events("run-1")
    event_types = [e.type for e in retained]
    assert RUN_STARTED in event_types
    assert RUN_COMPLETED in event_types


@pytest.mark.asyncio
async def test_run_main_agent_dispatches_task_plan_before_synthesis(tmp_dir):
    import os
    os.makedirs(os.path.join(tmp_dir, ".git"))
    child_output = json.dumps({
        "status": "success",
        "summary": "checked",
        "findings": [],
        "uncertainties": [],
        "notes": [],
    })
    model = FakeModel([
        ModelResponse(content=child_output, tool_use_blocks=[]),
        ModelResponse(content="ok", tool_use_blocks=[]),
    ])
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
        repo_root=tmp_dir,
        deps=deps,
        run_manager=mgr,
    )

    user_messages = [
        m for m in model.captured_messages[-1]
        if m.role == Role.USER
    ]
    assert len(user_messages) >= 1
    payload = json.loads(user_messages[0].content)
    assert "task_plan" not in payload
    assert payload["task_plan_summary"]["task_count"] == 1
    assert payload["task_results"][0]["task_id"] == "t1"
    assert payload["task_results"][0]["parse_status"] == "valid"


@pytest.mark.asyncio
async def test_run_main_agent_builds_runtime_without_blocking_event_loop(tmp_dir):
    model = FakeModel([ModelResponse(content="ok", tool_use_blocks=[])])
    deps = _make_deps_with_fake_model(model, tmp_dir)
    original_build_main_runtime = deps.build_main_runtime

    def slow_build_main_runtime(**kwargs):
        time.sleep(0.1)
        return original_build_main_runtime(**kwargs)

    deps.build_main_runtime = slow_build_main_runtime
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")

    task = asyncio.create_task(run_main_agent(
        run_id="run-1",
        context_id="ctx-1",
        task_plan={"context_id": "ctx-1", "tasks": [], "routes": []},
        pr_context=None,
        repo_root=tmp_dir,
        deps=deps,
        run_manager=mgr,
    ))

    started_at = asyncio.get_running_loop().time()
    await asyncio.sleep(0.01)
    elapsed = asyncio.get_running_loop().time() - started_at
    assert elapsed < 0.05
    await task


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

    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_run_main_agent_publishes_tool_and_subagent_events(tmp_dir):
    task_plan = {
        "context_id": "ctx-1",
        "tasks": [{"task_id": "t1", "task_type": "security_context"}],
        "routes": [{"task_type": "security_context", "agent_type": "security-context-agent", "max_steps": 2}],
    }
    child_output = json.dumps({
        "status": "success",
        "summary": "checked",
        "findings": [],
        "uncertainties": [],
        "notes": [],
    })
    model = FakeModel([
        ModelResponse(content=child_output, tool_use_blocks=[]),
        ModelResponse(content="done", tool_use_blocks=[]),
    ])
    deps = _make_deps_with_fake_model(model, tmp_dir)
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")

    with tempfile.TemporaryDirectory() as repo_root:
        import os
        os.makedirs(os.path.join(repo_root, ".git"))
        result = await run_main_agent(
            run_id="run-1",
            context_id="ctx-1",
            task_plan=task_plan,
            pr_context=None,
            repo_root=repo_root,
            deps=deps,
            run_manager=mgr,
        )

    assert result["status"] == "completed"
    event_types = [event.type for event in mgr.get_retained_events("run-1")]
    assert RUN_STARTED in event_types
    assert TOOL_CALL in event_types
    assert SUBAGENT_STARTED in event_types
    assert TOOL_RESULT in event_types
    assert RUN_COMPLETED in event_types


@pytest.mark.asyncio
async def test_run_main_agent_returns_partial_results_when_synthesis_fails(tmp_dir):
    import os
    os.makedirs(os.path.join(tmp_dir, ".git"), exist_ok=True)
    child_output = json.dumps({
        "status": "success",
        "summary": "checked",
        "findings": [],
        "uncertainties": [],
        "notes": [],
    })

    class SynthesisFailingModel(ModelClient):
        def __init__(self):
            self.calls = 0

        async def chat(self, messages, tool_schemas=None):
            self.calls += 1
            if self.calls == 1:
                return ModelResponse(content=child_output, tool_use_blocks=[])
            raise RuntimeError("synthesis unavailable")

    deps = _make_deps_with_fake_model(SynthesisFailingModel(), tmp_dir)
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")

    result = await run_main_agent(
        run_id="run-1",
        context_id="ctx-1",
        task_plan={
            "context_id": "ctx-1",
            "tasks": [{"task_id": "t1", "task_type": "security_context"}],
            "routes": [],
        },
        pr_context=None,
        repo_root=tmp_dir,
        deps=deps,
        run_manager=mgr,
    )

    assert result["status"] == "completed"
    assert "partial results" in result["summary"]
    assert result["task_summaries"][0]["parse_status"] == "valid"
    assert mgr.get_run("run-1").status.value == "completed"


# --- Task 2.7: Main runner tests for active message.delta RunEvent publication ---

class StreamingFakeModel(ModelClient):
    """Fake model that simulates streaming for main runner tests."""
    def __init__(self, response: ModelResponse, deltas: list[str] | None = None) -> None:
        self._response = response
        self._deltas = deltas or []

    async def chat(self, messages, tool_schemas=None):
        return self._response

    async def chat_stream(self, messages, tool_schemas=None, on_text_delta=None):
        for d in self._deltas:
            if on_text_delta:
                on_text_delta(d)
        return self._response


@pytest.mark.asyncio
async def test_run_main_agent_publishes_message_delta_events(tmp_dir):
    model = StreamingFakeModel(
        ModelResponse(content="Review complete. No issues found.", tool_use_blocks=[]),
        deltas=["Review complete.", " No issues found."],
    )
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
    assert MESSAGE_DELTA in captured_events

    retained = mgr.get_retained_events("run-1")
    delta_events = [e for e in retained if e.type == MESSAGE_DELTA]
    assert len(delta_events) == 2
    assert delta_events[0].payload["text"] == "Review complete."
    assert delta_events[1].payload["text"] == " No issues found."
    assert delta_events[0].sequence < delta_events[1].sequence


@pytest.mark.asyncio
async def test_message_delta_payload_bounded(tmp_dir):
    """message.delta payloads contain bounded text only."""
    long_text = "x" * 2000
    model = StreamingFakeModel(
        ModelResponse(content=long_text, tool_use_blocks=[]),
        deltas=[long_text],
    )
    deps = _make_deps_with_fake_model(model, tmp_dir)
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")

    await run_main_agent(
        run_id="run-1",
        context_id="ctx-1",
        task_plan={"context_id": "ctx-1", "tasks": [], "routes": []},
        pr_context=None,
        repo_root="/repo",
        deps=deps,
        run_manager=mgr,
    )

    retained = mgr.get_retained_events("run-1")
    delta_events = [e for e in retained if e.type == MESSAGE_DELTA]
    assert len(delta_events) == 1
    # Text should be truncated to MAX_VISIBLE_DELTA_CHARS (1000)
    assert len(delta_events[0].payload["text"]) <= 1000
