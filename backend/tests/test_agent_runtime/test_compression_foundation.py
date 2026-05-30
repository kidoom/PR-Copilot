"""Tests for compression engine foundation."""
from __future__ import annotations

import pytest

from backend.agent.model.messages import Message, Role, ToolResultBlock, ToolUseBlock
from backend.agent.runtime.compression.config import CompressionConfig
from backend.agent.runtime.compression.estimation import (
    estimate_message_tokens,
    estimate_messages_tokens,
    estimate_tokens,
)
from backend.agent.runtime.compression.repair import repair_tool_message_pairs
from backend.agent.runtime.compression.recent import select_recent_messages


# ============================================================================
# Token Estimation Tests
# ============================================================================


class TestTokenEstimation:
    def test_estimate_tokens_empty(self):
        assert estimate_tokens("") == 0

    def test_estimate_tokens_short(self):
        # 4 chars = 1 token
        assert estimate_tokens("test") == 1

    def test_estimate_tokens_long(self):
        # 100 chars = 25 tokens
        text = "a" * 100
        assert estimate_tokens(text) == 25

    def test_estimate_message_tokens_string(self):
        msg = Message(role=Role.USER, content="Hello, world!")
        tokens = estimate_message_tokens(msg)
        # 4 overhead + ~3 for content
        assert tokens >= 4
        assert tokens <= 10

    def test_estimate_message_tokens_empty(self):
        msg = Message(role=Role.USER, content="")
        tokens = estimate_message_tokens(msg)
        # Just overhead
        assert tokens == 4

    def test_estimate_message_tokens_tool_use(self):
        msg = Message(
            role=Role.ASSISTANT,
            content=[ToolUseBlock(
                tool_use_id="123",
                name="read_file",
                input={"path": "/test/file.py"},
            )],
        )
        tokens = estimate_message_tokens(msg)
        assert tokens > 4  # Overhead + tool block

    def test_estimate_message_tokens_tool_result(self):
        msg = Message(
            role=Role.TOOL,
            content=[ToolResultBlock(
                tool_use_id="123",
                content="file content here" * 100,
            )],
        )
        tokens = estimate_message_tokens(msg)
        assert tokens > 50  # Large content

    def test_estimate_messages_tokens_multiple(self):
        messages = [
            Message(role=Role.USER, content="Hello"),
            Message(role=Role.ASSISTANT, content="Hi there"),
            Message(role=Role.USER, content="How are you?"),
        ]
        total = estimate_messages_tokens(messages)
        assert total > 10


# ============================================================================
# Tool Pair Repair Tests
# ============================================================================


class TestToolPairRepair:
    def test_empty_messages(self):
        assert repair_tool_message_pairs([]) == []

    def test_no_orphans(self):
        messages = [
            Message(role=Role.USER, content="test"),
            Message(role=Role.ASSISTANT, content=[
                ToolUseBlock(tool_use_id="1", name="tool", input={}),
            ]),
            Message(role=Role.TOOL, content=[
                ToolResultBlock(tool_use_id="1", content="result"),
            ]),
        ]
        repaired = repair_tool_message_pairs(messages)
        assert len(repaired) == 3

    def test_orphan_tool_result_removed(self):
        messages = [
            Message(role=Role.TOOL, content=[
                ToolResultBlock(tool_use_id="orphan", content="result"),
            ]),
            Message(role=Role.USER, content="test"),
        ]
        repaired = repair_tool_message_pairs(messages)
        # Orphan tool result should be removed
        assert len(repaired) == 1
        assert repaired[0].role == Role.USER

    def test_orphan_tool_use_removed(self):
        messages = [
            Message(role=Role.ASSISTANT, content=[
                ToolUseBlock(tool_use_id="orphan", name="tool", input={}),
            ]),
            Message(role=Role.USER, content="test"),
        ]
        repaired = repair_tool_message_pairs(messages)
        # Orphan tool use should be removed (no text content)
        assert len(repaired) == 1
        assert repaired[0].role == Role.USER

    def test_orphan_tool_use_keeps_text(self):
        messages = [
            Message(role=Role.ASSISTANT, content=[
                "I'll use a tool",
                ToolUseBlock(tool_use_id="orphan", name="tool", input={}),
            ]),
        ]
        repaired = repair_tool_message_pairs(messages)
        # Should keep text, remove orphan tool use
        assert len(repaired) == 1
        assert repaired[0].content == "I'll use a tool"

    def test_mixed_orphans(self):
        messages = [
            Message(role=Role.ASSISTANT, content=[
                ToolUseBlock(tool_use_id="valid", name="tool", input={}),
                ToolUseBlock(tool_use_id="orphan_use", name="tool", input={}),
            ]),
            Message(role=Role.TOOL, content=[
                ToolResultBlock(tool_use_id="valid", content="ok"),
                ToolResultBlock(tool_use_id="orphan_result", content="bad"),
            ]),
        ]
        repaired = repair_tool_message_pairs(messages)
        # Should keep valid pair, remove orphans
        assert len(repaired) == 2
        # First message should only have valid tool use
        assert isinstance(repaired[0].content, list)
        assert len(repaired[0].content) == 1
        assert repaired[0].content[0].tool_use_id == "valid"
        # Second message should only have valid tool result
        assert isinstance(repaired[1].content, list)
        assert len(repaired[1].content) == 1
        assert repaired[1].content[0].tool_use_id == "valid"


# ============================================================================
# Recent Message Selection Tests
# ============================================================================


class TestRecentMessages:
    def test_empty_messages(self):
        assert select_recent_messages([], 5) == []

    def test_zero_count(self):
        messages = [Message(role=Role.USER, content="test")]
        assert select_recent_messages(messages, 0) == []

    def test_select_recent(self):
        messages = [
            Message(role=Role.USER, content="msg1"),
            Message(role=Role.ASSISTANT, content="msg2"),
            Message(role=Role.USER, content="msg3"),
            Message(role=Role.ASSISTANT, content="msg4"),
            Message(role=Role.USER, content="msg5"),
        ]
        recent = select_recent_messages(messages, 3)
        assert len(recent) == 3
        assert recent[0].content == "msg3"
        assert recent[1].content == "msg4"
        assert recent[2].content == "msg5"

    def test_select_more_than_available(self):
        messages = [
            Message(role=Role.USER, content="msg1"),
            Message(role=Role.ASSISTANT, content="msg2"),
        ]
        recent = select_recent_messages(messages, 10)
        assert len(recent) == 2

    def test_select_repairs_broken_pairs(self):
        messages = [
            Message(role=Role.USER, content="msg1"),
            Message(role=Role.ASSISTANT, content=[
                ToolUseBlock(tool_use_id="1", name="tool", input={}),
            ]),
            Message(role=Role.TOOL, content=[
                ToolResultBlock(tool_use_id="1", content="result"),
            ]),
            Message(role=Role.USER, content="msg2"),
        ]
        # Select 3 messages - should include tool use, tool result, and user msg
        recent = select_recent_messages(messages, 3)
        assert len(recent) == 3

    def test_select_repairs_orphan_in_selection(self):
        messages = [
            Message(role=Role.ASSISTANT, content=[
                ToolUseBlock(tool_use_id="1", name="tool", input={}),
            ]),
            Message(role=Role.TOOL, content=[
                ToolResultBlock(tool_use_id="1", content="result"),
            ]),
            Message(role=Role.USER, content="msg1"),
        ]
        # Select only last 2 messages - tool result becomes orphan
        recent = select_recent_messages(messages, 2)
        # Orphan tool result should be removed
        assert len(recent) == 1
        assert recent[0].role == Role.USER


# ============================================================================
# CompressionConfig Tests
# ============================================================================


class TestCompressionConfig:
    def test_default_config(self):
        config = CompressionConfig.default()
        assert config.context_compression_enabled is True
        assert config.context_window_tokens == 128000

    def test_disabled_config(self):
        config = CompressionConfig.disabled()
        assert config.context_compression_enabled is False

    def test_auto_compact_threshold(self):
        config = CompressionConfig.default()
        # 128000 - 4000 - 8000 = 116000
        assert config.auto_compact_threshold == 116000

    def test_custom_config(self):
        config = CompressionConfig(
            context_window_tokens=64000,
            compact_max_summary_tokens=2000,
            auto_compact_buffer_tokens=4000,
        )
        assert config.auto_compact_threshold == 58000
