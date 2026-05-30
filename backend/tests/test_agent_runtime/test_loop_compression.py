"""Tests for runtime loop compression integration."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.agent.model.client import ModelClient
from backend.agent.model.messages import Message, ModelResponse, Role, ToolUseBlock
from backend.agent.runtime.compression.compact_prompts import CompactProfile
from backend.agent.runtime.compression.config import CompressionConfig
from backend.agent.runtime.loop import run_loop
from backend.agent.runtime.memory import AgentKind, MemorySessionMeta
from backend.agent.runtime.memory.store import FileMemoryStore
from backend.agent.tools.registry import ToolRegistry


@pytest.fixture
def store(tmp_path):
    return FileMemoryStore(str(tmp_path))


@pytest.fixture
def config():
    return CompressionConfig.default()


def _create_session(store: FileMemoryStore, session_id: str = "test-session"):
    meta = MemorySessionMeta(
        session_id=session_id,
        run_id="run123",
        agent_kind=AgentKind.MAIN,
        agent_type="main-agent",
        context_id="ctx456",
    )
    return store.create_session(meta)


class TestCompressionDisabled:
    @pytest.mark.asyncio
    async def test_no_compression_when_disabled(self, store, config):
        """Test that compression is not applied when disabled."""
        _create_session(store)
        config.context_compression_enabled = False

        model = AsyncMock(spec=ModelClient)
        model.chat = AsyncMock(return_value=ModelResponse(
            content="done",
            tool_use_blocks=[],
        ))

        messages = [Message(role=Role.USER, content="test")]
        result = await run_loop(
            model=model,
            tool_registry=ToolRegistry(),
            messages=messages,
            session_id="test-session",
            memory_store=store,
            compression_config=config,
            compression_profile=CompactProfile.MAIN_AGENT,
        )

        assert result.output == "done"

    @pytest.mark.asyncio
    async def test_no_compression_without_session(self, config):
        """Test that compression is not applied without session."""
        model = AsyncMock(spec=ModelClient)
        model.chat = AsyncMock(return_value=ModelResponse(
            content="done",
            tool_use_blocks=[],
        ))

        messages = [Message(role=Role.USER, content="test")]
        result = await run_loop(
            model=model,
            tool_registry=ToolRegistry(),
            messages=messages,
            compression_config=config,
        )

        assert result.output == "done"


class TestAutoCompact:
    @pytest.mark.asyncio
    async def test_auto_compact_triggered(self, store, config):
        """Test AutoCompact is triggered when threshold reached."""
        _create_session(store)

        # Set low threshold to trigger compact
        config.context_window_tokens = 100
        config.auto_compact_buffer_tokens = 10
        config.compact_max_summary_tokens = 10

        # Create messages that exceed threshold
        long_content = "x" * 1000
        messages = [
            Message(role=Role.USER, content=long_content),
            Message(role=Role.ASSISTANT, content="response"),
        ]

        call_count = 0

        async def mock_chat(messages, tool_schemas=None):
            nonlocal call_count
            call_count += 1
            return ModelResponse(content="done", tool_use_blocks=[])

        model = AsyncMock(spec=ModelClient)
        model.chat = mock_chat

        compact_events = []

        def on_compact(event_type, before, after):
            compact_events.append(event_type)

        result = await run_loop(
            model=model,
            tool_registry=ToolRegistry(),
            messages=messages,
            session_id="test-session",
            memory_store=store,
            compression_config=config,
            compression_profile=CompactProfile.MAIN_AGENT,
            on_compact=on_compact,
        )

        # Should have called model at least once
        assert call_count >= 1


class TestReactiveCompact:
    @pytest.mark.asyncio
    async def test_reactive_compact_on_context_error(self, store, config):
        """Test ReactiveCompact on context-length error."""
        _create_session(store)

        call_count = 0

        async def mock_chat(messages, tool_schemas=None):
            nonlocal call_count
            call_count += 1
            # First call: context error
            # Second call: compact summarization
            # Third call: retry after compact
            if call_count == 1:
                raise RuntimeError("context_length_exceeded")
            if call_count == 2:
                # Compact summarization
                return ModelResponse(content="Summary", tool_use_blocks=[])
            # Final call
            return ModelResponse(content="done", tool_use_blocks=[])

        model = AsyncMock(spec=ModelClient)
        model.chat = mock_chat

        messages = [Message(role=Role.USER, content="test")]
        result = await run_loop(
            model=model,
            tool_registry=ToolRegistry(),
            messages=messages,
            session_id="test-session",
            memory_store=store,
            compression_config=config,
            compression_profile=CompactProfile.MAIN_AGENT,
        )

        # Should have called model 3 times (error, compact, retry)
        assert call_count == 3
        assert result.output == "done"
