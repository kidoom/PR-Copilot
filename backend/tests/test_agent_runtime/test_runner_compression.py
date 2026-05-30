"""Tests for main and subagent runner compression wiring."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from backend.agent.model.client import ModelClient
from backend.agent.model.messages import Message, ModelResponse, Role
from backend.agent.runtime.compression.config import CompressionConfig
from backend.agent.runtime.main_runner import run_main_agent
from backend.agent.runtime.memory import AgentKind, MemorySessionMeta
from backend.agent.runtime.memory.store import FileMemoryStore
from backend.agent.runtime.run_manager import RunManager
from backend.deps import AgentDeps


@pytest.fixture
def store(tmp_path):
    return FileMemoryStore(str(tmp_path))


@pytest.fixture
def config():
    return CompressionConfig.default()


class TestMainRunnerCompression:
    @pytest.mark.asyncio
    async def test_main_runner_passes_compression_config(self, store, config):
        """Test that main runner passes compression config to run_loop."""
        config.context_compression_enabled = False

        model = AsyncMock(spec=ModelClient)
        model.chat = AsyncMock(return_value=ModelResponse(
            content="done",
            tool_use_blocks=[],
        ))

        deps = AgentDeps(
            model_config=None,
            subagent_registry=None,
            memory_store=store,
            compression_config=config,
        )
        deps.new_model = lambda: model

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

        assert "error" not in result
        assert result["output"] == "done"

    @pytest.mark.asyncio
    async def test_main_runner_creates_main_session(self, store, config):
        """Test that main runner creates a main memory session."""
        config.context_compression_enabled = False

        model = AsyncMock(spec=ModelClient)
        model.chat = AsyncMock(return_value=ModelResponse(
            content="done",
            tool_use_blocks=[],
        ))

        deps = AgentDeps(
            model_config=None,
            subagent_registry=None,
            memory_store=store,
            compression_config=config,
        )
        deps.new_model = lambda: model

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

        # Check that session was created
        sessions = store.list_sessions(agent_kind=AgentKind.MAIN)
        assert len(sessions) == 1
        assert sessions[0].agent_type == "main-agent"


class TestTaskToolCompressionBoundary:
    def test_task_tool_remains_compression_agnostic(self):
        """Test that TaskTool does not own compression logic."""
        from backend.agent.tools.task import TaskTool

        # TaskTool should not have compression-related attributes
        assert not hasattr(TaskTool, 'compression_config')
        assert not hasattr(TaskTool, 'execute_compact')
        assert not hasattr(TaskTool, 'micro_compact')
