"""Tests for compact execution core."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.agent.model.client import ModelClient
from backend.agent.model.messages import Message, ModelResponse, Role, ToolResultBlock, ToolUseBlock
from backend.agent.runtime.compression.compact import (
    build_summary_boundary_message,
    execute_compact,
    is_context_length_error,
    serialize_messages_for_compact,
    serialize_recent_messages_lightweight,
    snip_large_tool_result,
)
from backend.agent.runtime.compression.compact_prompts import CompactProfile
from backend.agent.runtime.compression.config import CompressionConfig


# ============================================================================
# Serialization Tests
# ============================================================================


class TestSerialization:
    def test_serialize_plain_text(self):
        messages = [
            Message(role=Role.USER, content="Hello"),
            Message(role=Role.ASSISTANT, content="Hi there"),
        ]
        result = serialize_messages_for_compact(messages)
        assert "[USER] Hello" in result
        assert "[ASSISTANT] Hi there" in result

    def test_serialize_tool_use(self):
        messages = [
            Message(role=Role.ASSISTANT, content=[
                ToolUseBlock(tool_use_id="1", name="read_file", input={"path": "/test"}),
            ]),
        ]
        result = serialize_messages_for_compact(messages)
        assert "[Tool Call: read_file]" in result
        assert "/test" in result

    def test_serialize_tool_result(self):
        messages = [
            Message(role=Role.TOOL, content=[
                ToolResultBlock(tool_use_id="1", content="file content here"),
            ]),
        ]
        result = serialize_messages_for_compact(messages)
        assert "[Tool Result] file content here" in result

    def test_serialize_tool_error(self):
        messages = [
            Message(role=Role.TOOL, content=[
                ToolResultBlock(tool_use_id="1", content="error message", is_error=True),
            ]),
        ]
        result = serialize_messages_for_compact(messages)
        assert "[Tool Error] error message" in result

    def test_serialize_snips_long_content(self):
        long_content = "x" * 1000
        messages = [
            Message(role=Role.TOOL, content=[
                ToolResultBlock(tool_use_id="1", content=long_content),
            ]),
        ]
        result = serialize_messages_for_compact(messages)
        # Should be snipped to 500 chars
        assert len(result) < 800


# ============================================================================
# Context-Length Error Detection Tests
# ============================================================================


class TestContextLengthError:
    def test_context_length_in_type_name(self):
        class ContextLengthError(Exception):
            pass

        error = ContextLengthError("something failed")
        assert is_context_length_error(error) is True

    def test_max_token_in_type_name(self):
        class MaxTokenExceeded(Exception):
            pass

        error = MaxTokenExceeded("something failed")
        assert is_context_length_error(error) is True

    def test_context_length_in_message(self):
        error = RuntimeError("context length exceeded")
        assert is_context_length_error(error) is True

    def test_too_many_tokens_in_message(self):
        error = RuntimeError("too many tokens in request")
        assert is_context_length_error(error) is True

    def test_non_context_error(self):
        error = ValueError("some other error")
        assert is_context_length_error(error) is False


# ============================================================================
# Summary Boundary Tests
# ============================================================================


class TestSummaryBoundary:
    def test_build_summary_boundary(self):
        msg = build_summary_boundary_message("This is a summary")
        assert msg.role == Role.USER
        assert "[Context Summary]" in msg.content
        assert "This is a summary" in msg.content


# ============================================================================
# Snip Tests
# ============================================================================


class TestSnip:
    def test_short_content_unchanged(self):
        content = "short"
        assert snip_large_tool_result(content, 100) == "short"

    def test_long_content_snipped(self):
        content = "x" * 2000
        result = snip_large_tool_result(content, 1000)
        assert len(result) < 1100
        assert "snipped" in result

    def test_default_max_chars(self):
        content = "x" * 3000
        result = snip_large_tool_result(content)
        assert len(result) < 2500
        assert "snipped" in result


# ============================================================================
# Lightweight Serialization Tests
# ============================================================================


class TestLightweightSerialization:
    def test_serialize_plain_messages(self):
        messages = [
            Message(role=Role.USER, content="Hello"),
            Message(role=Role.ASSISTANT, content="Hi"),
        ]
        result = serialize_recent_messages_lightweight(messages)
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"

    def test_serialize_snips_large_tool_result(self):
        large_content = "x" * 5000
        messages = [
            Message(role=Role.TOOL, content=[
                ToolResultBlock(tool_use_id="1", content=large_content),
            ]),
        ]
        result = serialize_recent_messages_lightweight(messages)
        assert len(result) == 1
        block = result[0]["content"][0]
        assert len(block["content"]) < 3000


# ============================================================================
# Execute Compact Tests
# ============================================================================


class TestExecuteCompact:
    @pytest.mark.asyncio
    async def test_successful_compact(self):
        mock_model = AsyncMock(spec=ModelClient)
        mock_model.chat = AsyncMock(return_value=ModelResponse(
            content="Summary of conversation",
            tool_use_blocks=[],
        ))

        messages = [
            Message(role=Role.USER, content="Review this PR"),
            Message(role=Role.ASSISTANT, content="I'll review it"),
        ]

        config = CompressionConfig.default()
        result = await execute_compact(
            model=mock_model,
            messages=messages,
            profile=CompactProfile.MAIN_AGENT,
            config=config,
        )

        assert result is not None
        summary, recent = result
        assert summary == "Summary of conversation"
        assert len(recent) == 2

    @pytest.mark.asyncio
    async def test_empty_messages(self):
        mock_model = AsyncMock(spec=ModelClient)
        config = CompressionConfig.default()

        result = await execute_compact(
            model=mock_model,
            messages=[],
            profile=CompactProfile.MAIN_AGENT,
            config=config,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_empty_summary_failure(self):
        mock_model = AsyncMock(spec=ModelClient)
        mock_model.chat = AsyncMock(return_value=ModelResponse(
            content="",
            tool_use_blocks=[],
        ))

        messages = [
            Message(role=Role.USER, content="Review this PR"),
        ]

        config = CompressionConfig.default()
        result = await execute_compact(
            model=mock_model,
            messages=messages,
            profile=CompactProfile.MAIN_AGENT,
            config=config,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_context_length_retry(self):
        call_count = 0

        async def mock_chat(messages, tool_schemas=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("context_length_exceeded")
            return ModelResponse(content="Summary after retry", tool_use_blocks=[])

        mock_model = AsyncMock(spec=ModelClient)
        mock_model.chat = mock_chat

        messages = [
            Message(role=Role.USER, content="msg1"),
            Message(role=Role.ASSISTANT, content="msg2"),
            Message(role=Role.USER, content="msg3"),
            Message(role=Role.ASSISTANT, content="msg4"),
        ]

        config = CompressionConfig.default()
        result = await execute_compact(
            model=mock_model,
            messages=messages,
            profile=CompactProfile.MAIN_AGENT,
            config=config,
        )

        assert result is not None
        assert call_count == 2
