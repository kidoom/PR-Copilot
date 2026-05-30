from __future__ import annotations

import json

import pytest

from backend.agent.model.client import ModelClient
from backend.agent.model.messages import Message, ModelResponse, ToolUseBlock
from backend.deps import create_agent_deps, get_agent_deps, preload_agent_deps, set_agent_deps


class FakeModel(ModelClient):
    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[list[Message]] = []

    async def chat(self, messages: list[Message], tool_schemas=None) -> ModelResponse:
        self.calls.append(list(messages))
        return self._responses.pop(0)


def test_create_agent_deps_preloads_static_subagents(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    deps = create_agent_deps()

    assert deps.model_config.api_key == "test-key"
    assert "security-context-agent" in deps.subagent_registry.names()
    assert "test-context-agent" in deps.subagent_registry.names()


def test_global_agent_deps_cache_can_preload(monkeypatch):
    set_agent_deps(None)
    monkeypatch.setenv("OPENAI_MODEL", "test-model")

    deps = preload_agent_deps()

    assert get_agent_deps() is deps
    assert deps.model_config.model == "test-model"


def test_build_main_messages_includes_task_plan():
    deps = create_agent_deps()
    task_plan = {"context_id": "ctx1", "tasks": [], "routes": []}

    messages = deps.build_main_messages(task_plan)

    assert messages[0].role.value == "system"
    assert "task" in messages[0].content
    payload = json.loads(messages[1].content)
    assert payload["task_plan"]["context_id"] == "ctx1"


@pytest.mark.asyncio
async def test_main_runtime_registers_task_tool_and_records_child_bundle():
    deps = create_agent_deps()
    model = FakeModel([ModelResponse(content="child done", tool_use_blocks=[])])
    task_plan = {
        "context_id": "ctx1",
        "tasks": [],
        "routes": [],
    }
    runtime = deps.build_main_runtime(
        model=model,
        task_plan=task_plan,
        pr_context=None,
        repo_root="D:/repo",
        parent_session_id="parent",
    )

    task_tool = runtime.tool_registry.resolve("task")
    assert task_tool is not None

    result = await runtime.task_tool.run(
        prompt="inspect auth",
        agent_type="security-context-agent",
        task={
            "context_id": "ctx1",
            "task_id": "task1",
            "task_type": "security_context",
            "budget": {"max_searches": 2, "max_files": 3, "max_tokens": 1000},
        },
    )

    assert result.child_session_id in runtime.child_bundles
    bundle = runtime.child_bundles[result.child_session_id]
    assert bundle.session.context_id == "ctx1"
    assert bundle.session.task_id == "task1"
    assert bundle.session.repo_root == "D:/repo"
    assert bundle.session.budget.max_searches == 2
