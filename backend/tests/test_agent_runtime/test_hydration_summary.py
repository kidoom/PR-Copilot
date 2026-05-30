"""Tests for memory hydration with standardized summary payloads."""
from __future__ import annotations

import pytest

from backend.agent.model.messages import Message, Role, ToolResultBlock, ToolUseBlock
from backend.agent.runtime.memory import (
    AgentKind,
    EntryType,
    MemorySessionMeta,
    append_message,
    append_summary,
    hydrate_messages,
)
from backend.agent.runtime.memory.store import FileMemoryStore


@pytest.fixture
def store(tmp_path):
    """Create a temporary FileMemoryStore."""
    return FileMemoryStore(str(tmp_path))


def _create_session(store: FileMemoryStore, session_id: str = "test-session"):
    """Helper to create a test session."""
    meta = MemorySessionMeta(
        session_id=session_id,
        run_id="run123",
        agent_kind=AgentKind.MAIN,
        agent_type="main-agent",
        context_id="ctx456",
    )
    return store.create_session(meta)


class TestStandardizedSummaryPayload:
    """Test hydration with standardized summary fields."""

    def test_hydrate_with_summary_field(self, store):
        """Test that 'summary' field is accepted."""
        _create_session(store)

        append_message(store, "test-session", {"role": "user", "content": "msg1"})
        append_message(store, "test-session", {"role": "assistant", "content": "msg2"})

        # Append summary with standardized field
        from backend.agent.runtime.memory.models import TranscriptEntry
        entry = TranscriptEntry.create(
            session_id="test-session",
            entry_type=EntryType.SUMMARY,
            payload={
                "reason": "auto_compact",
                "summary": "Summary of conversation",
                "before_message_count": 2,
                "after_message_count": 1,
                "recent_messages": [],
            },
        )
        store.append_entry(entry)

        messages = hydrate_messages(store, "test-session")
        assert len(messages) >= 1
        assert "Summary of conversation" in messages[0].content
        assert "auto_compact" in messages[0].content

    def test_hydrate_with_legacy_summary_text(self, store):
        """Test backward compatibility with 'summary_text' field."""
        _create_session(store)

        append_message(store, "test-session", {"role": "user", "content": "msg1"})

        # Append summary with legacy field
        from backend.agent.runtime.memory.models import TranscriptEntry
        entry = TranscriptEntry.create(
            session_id="test-session",
            entry_type=EntryType.SUMMARY,
            payload={
                "summary_text": "Legacy summary",
            },
        )
        store.append_entry(entry)

        messages = hydrate_messages(store, "test-session")
        assert len(messages) >= 1
        assert "Legacy summary" in messages[0].content

    def test_hydrate_summary_with_recent_messages(self, store):
        """Test that recent_messages are preserved after summary."""
        _create_session(store)

        append_message(store, "test-session", {"role": "user", "content": "old msg"})

        # Append summary with recent messages
        from backend.agent.runtime.memory.models import TranscriptEntry
        entry = TranscriptEntry.create(
            session_id="test-session",
            entry_type=EntryType.SUMMARY,
            payload={
                "reason": "auto_compact",
                "summary": "Summary",
                "recent_messages": [
                    {"role": "user", "content": "recent user msg"},
                    {"role": "assistant", "content": "recent assistant msg"},
                ],
            },
        )
        store.append_entry(entry)

        messages = hydrate_messages(store, "test-session")
        # Should have summary + 2 recent messages
        assert len(messages) == 3
        assert "Summary" in messages[0].content
        assert messages[1].content == "recent user msg"
        assert messages[2].content == "recent assistant msg"

    def test_hydrate_ignores_non_model_entries(self, store):
        """Test that non-model entries are ignored."""
        _create_session(store)

        from backend.agent.runtime.memory.append import (
            append_event,
            append_todo_state,
            append_evidence_package,
        )

        append_message(store, "test-session", {"role": "user", "content": "msg1"})
        append_event(store, "test-session", {"event_type": "test"})
        append_todo_state(store, "test-session", [{"content": "task", "status": "done"}])
        append_evidence_package(store, "test-session", {"task_id": "t1", "status": "found_context"})
        append_message(store, "test-session", {"role": "assistant", "content": "msg2"})

        messages = hydrate_messages(store, "test-session")
        # Should only have 2 messages, not events/todos/evidence
        assert len(messages) == 2

    def test_hydrate_repairs_tool_pairs(self, store):
        """Test that tool pairs are repaired after hydration."""
        _create_session(store)

        # Add messages with orphan tool result
        append_message(store, "test-session", {
            "role": "assistant",
            "content": [
                {"tool_use_id": "1", "name": "read_file", "input": {}},
            ],
        })
        append_message(store, "test-session", {
            "role": "tool",
            "content": [
                {"tool_use_id": "1", "content": "file content"},
            ],
        })

        messages = hydrate_messages(store, "test-session")
        assert len(messages) == 2

    def test_hydrate_empty_session(self, store):
        """Test hydrating empty session."""
        _create_session(store)
        messages = hydrate_messages(store, "test-session")
        assert messages == []
