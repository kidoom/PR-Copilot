"""Tests for PR RepoContext MicroCompact."""
from __future__ import annotations

import pytest

from backend.agent.model.messages import Message, Role, ToolResultBlock, ToolUseBlock
from backend.agent.runtime.compression.micro_compact import (
    COMPACTABLE_TOOLS,
    EXCLUDED_TOOLS,
    build_placeholder,
    deep_copy_messages,
    is_compactable_tool_result,
    is_compactable_tool_use,
    micro_compact_messages,
)


# ============================================================================
# Deep Copy Tests
# ============================================================================


class TestDeepCopy:
    def test_deep_copy_creates_independent_copy(self):
        original = [
            Message(role=Role.USER, content="test"),
            Message(role=Role.ASSISTANT, content=[
                ToolUseBlock(tool_use_id="1", name="read_file_patch", input={"path": "/test"}),
            ]),
        ]
        copied = deep_copy_messages(original)

        # Modify copied content (not frozen fields)
        copied[0].content = "modified"

        # Original should be unchanged
        assert original[0].content == "test"

    def test_deep_copy_empty(self):
        assert deep_copy_messages([]) == []


# ============================================================================
# Compactable Tool Detection Tests
# ============================================================================


class TestCompactableDetection:
    def test_compactable_tool_use(self):
        for tool_name in COMPACTABLE_TOOLS:
            block = ToolUseBlock(tool_use_id="1", name=tool_name, input={})
            assert is_compactable_tool_use(block) is True

    def test_non_compactable_tool_use(self):
        block = ToolUseBlock(tool_use_id="1", name="other_tool", input={})
        assert is_compactable_tool_use(block) is False

    def test_excluded_tool_use(self):
        for tool_name in EXCLUDED_TOOLS:
            block = ToolUseBlock(tool_use_id="1", name=tool_name, input={})
            # Excluded tools are not in COMPACTABLE_TOOLS
            assert is_compactable_tool_use(block) is False

    def test_compactable_tool_result(self):
        block = ToolResultBlock(tool_use_id="1", content="x" * 2000)
        assert is_compactable_tool_result(block, "read_file_patch", False, 1000) is True

    def test_tool_result_too_small(self):
        block = ToolResultBlock(tool_use_id="1", content="small")
        assert is_compactable_tool_result(block, "read_file", False, 1000) is False

    def test_tool_result_error(self):
        block = ToolResultBlock(tool_use_id="1", content="x" * 2000, is_error=True)
        assert is_compactable_tool_result(block, "read_file", False, 1000) is False

    def test_tool_result_excluded_tool(self):
        block = ToolResultBlock(tool_use_id="1", content="x" * 2000)
        assert is_compactable_tool_result(block, "finish_context_package", False, 1000) is False

    def test_tool_result_non_compactable_tool(self):
        block = ToolResultBlock(tool_use_id="1", content="x" * 2000)
        assert is_compactable_tool_result(block, "other_tool", False, 1000) is False


# ============================================================================
# Placeholder Tests
# ============================================================================


class TestPlaceholder:
    def test_build_placeholder_basic(self):
        placeholder = build_placeholder("read_file", 5000)
        assert "read_file" in placeholder
        assert "5,000 chars" in placeholder

    def test_build_placeholder_with_path(self):
        placeholder = build_placeholder("read_file", 5000, {"path": "/test/file.py"})
        assert "read_file" in placeholder
        assert "/test/file.py" in placeholder

    def test_build_placeholder_with_query(self):
        placeholder = build_placeholder("search_repo", 3000, {"query": "def main"})
        assert "search_repo" in placeholder
        assert "def main" in placeholder

    def test_build_placeholder_with_filename(self):
        placeholder = build_placeholder("read_file_patch", 2000, {"filename": "main.py"})
        assert "read_file_patch" in placeholder
        assert "main.py" in placeholder


# ============================================================================
# MicroCompact Tests
# ============================================================================


class TestMicroCompact:
    def test_empty_messages(self):
        assert micro_compact_messages([]) == []

    def test_compacts_old_large_result(self):
        messages = [
            Message(role=Role.USER, content="test"),
            Message(role=Role.ASSISTANT, content=[
                ToolUseBlock(tool_use_id="1", name="read_file_patch", input={"path": "/test"}),
            ]),
            Message(role=Role.TOOL, content=[
                ToolResultBlock(tool_use_id="1", content="x" * 2000),
            ]),
            Message(role=Role.ASSISTANT, content="done"),
        ]
        compacted = micro_compact_messages(messages, recent_count=0, min_chars=1000)

        # Should be compacted
        assert isinstance(compacted[2].content, list)
        assert "[Compacted: read_file_patch]" in compacted[2].content[0].content

        # Original should be unchanged
        assert isinstance(messages[2].content, list)
        assert messages[2].content[0].content == "x" * 2000

    def test_keeps_recent_results(self):
        messages = [
            Message(role=Role.USER, content="test"),
            Message(role=Role.ASSISTANT, content=[
                ToolUseBlock(tool_use_id="1", name="read_file_patch", input={}),
            ]),
            Message(role=Role.TOOL, content=[
                ToolResultBlock(tool_use_id="1", content="x" * 2000),
            ]),
            Message(role=Role.ASSISTANT, content=[
                ToolUseBlock(tool_use_id="2", name="read_file_patch", input={}),
            ]),
            Message(role=Role.TOOL, content=[
                ToolResultBlock(tool_use_id="2", content="y" * 2000),
            ]),
        ]
        compacted = micro_compact_messages(messages, recent_count=1, min_chars=1000)

        # First result should be compacted
        assert isinstance(compacted[2].content, list)
        assert "[Compacted:" in compacted[2].content[0].content

        # Recent result should be unchanged
        assert isinstance(compacted[4].content, list)
        assert compacted[4].content[0].content == "y" * 2000

    def test_keeps_small_results(self):
        messages = [
            Message(role=Role.USER, content="test"),
            Message(role=Role.ASSISTANT, content=[
                ToolUseBlock(tool_use_id="1", name="read_file_patch", input={}),
            ]),
            Message(role=Role.TOOL, content=[
                ToolResultBlock(tool_use_id="1", content="small"),
            ]),
        ]
        compacted = micro_compact_messages(messages, recent_count=0, min_chars=1000)

        # Should not be compacted (too small)
        assert isinstance(compacted[2].content, list)
        assert compacted[2].content[0].content == "small"

    def test_keeps_error_results(self):
        messages = [
            Message(role=Role.USER, content="test"),
            Message(role=Role.ASSISTANT, content=[
                ToolUseBlock(tool_use_id="1", name="read_file_patch", input={}),
            ]),
            Message(role=Role.TOOL, content=[
                ToolResultBlock(tool_use_id="1", content="x" * 2000, is_error=True),
            ]),
        ]
        compacted = micro_compact_messages(messages, recent_count=0, min_chars=1000)

        # Should not be compacted (error)
        assert isinstance(compacted[2].content, list)
        assert compacted[2].content[0].content == "x" * 2000

    def test_keeps_excluded_tool_results(self):
        messages = [
            Message(role=Role.USER, content="test"),
            Message(role=Role.ASSISTANT, content=[
                ToolUseBlock(tool_use_id="1", name="finish_context_package", input={}),
            ]),
            Message(role=Role.TOOL, content=[
                ToolResultBlock(tool_use_id="1", content="x" * 2000),
            ]),
        ]
        compacted = micro_compact_messages(messages, recent_count=0, min_chars=1000)

        # Should not be compacted (excluded tool)
        assert isinstance(compacted[2].content, list)
        assert compacted[2].content[0].content == "x" * 2000

    def test_keeps_non_compactable_tool_results(self):
        messages = [
            Message(role=Role.USER, content="test"),
            Message(role=Role.ASSISTANT, content=[
                ToolUseBlock(tool_use_id="1", name="other_tool", input={}),
            ]),
            Message(role=Role.TOOL, content=[
                ToolResultBlock(tool_use_id="1", content="x" * 2000),
            ]),
        ]
        compacted = micro_compact_messages(messages, recent_count=0, min_chars=1000)

        # Should not be compacted (not in whitelist)
        assert isinstance(compacted[2].content, list)
        assert compacted[2].content[0].content == "x" * 2000

    def test_compacts_multiple_tools(self):
        tool_names = ["read_file_patch", "search_repo", "search_tests_for", "search_diff"]
        for tool_name in tool_names:
            messages = [
                Message(role=Role.USER, content="test"),
                Message(role=Role.ASSISTANT, content=[
                    ToolUseBlock(tool_use_id="1", name=tool_name, input={}),
                ]),
                Message(role=Role.TOOL, content=[
                    ToolResultBlock(tool_use_id="1", content="x" * 2000),
                ]),
            ]
            compacted = micro_compact_messages(messages, recent_count=0, min_chars=1000)

            assert isinstance(compacted[2].content, list)
            assert "[Compacted:" in compacted[2].content[0].content, f"Failed for {tool_name}"

    def test_skips_evidence_critical_tools(self):
        """read_repo_file is evidence-critical and must never be compacted."""
        messages = [
            Message(role=Role.USER, content="test"),
            Message(role=Role.ASSISTANT, content=[
                ToolUseBlock(tool_use_id="1", name="read_repo_file", input={}),
            ]),
            Message(role=Role.TOOL, content=[
                ToolResultBlock(tool_use_id="1", content="x" * 2000),
            ]),
        ]
        compacted = micro_compact_messages(messages, recent_count=0, min_chars=1000)

        assert isinstance(compacted[2].content, list)
        assert compacted[2].content[0].content == "x" * 2000

    def test_preserves_user_messages(self):
        messages = [
            Message(role=Role.USER, content="user message"),
            Message(role=Role.ASSISTANT, content="assistant message"),
        ]
        compacted = micro_compact_messages(messages)

        assert compacted[0].content == "user message"
        assert compacted[1].content == "assistant message"
