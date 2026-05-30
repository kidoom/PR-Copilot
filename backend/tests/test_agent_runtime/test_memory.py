"""Tests for agent memory JSONL persistence."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from backend.agent.runtime.memory import (
    AgentKind,
    EntryType,
    MemorySessionMeta,
    TranscriptEntry,
    append_evidence_package,
    append_event,
    append_message,
    append_summary,
    append_todo_state,
    build_main_session_id,
    build_subagent_session_id,
    hydrate_messages,
    validate_session_id,
)
from backend.agent.runtime.memory.store import (
    AppendToUnknownSessionError,
    FileMemoryStore,
    InvalidSessionError,
    SessionAlreadyExistsError,
    SessionNotFoundError,
)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def store(temp_dir):
    """Create a FileMemoryStore for tests."""
    return FileMemoryStore(temp_dir)


# ============================================================================
# 6.1 Session ID Tests
# ============================================================================


class TestSessionId:
    def test_valid_session_ids(self):
        """Test valid session id patterns."""
        assert validate_session_id("abc123")
        assert validate_session_id("main-run123-ctx456")
        assert validate_session_id("sub-security-context-agent-run123")
        assert validate_session_id("session_with_underscores")
        assert validate_session_id("a" * 128)  # Max length

    def test_invalid_session_ids(self):
        """Test invalid session id patterns."""
        assert not validate_session_id("")
        assert not validate_session_id("a" * 129)  # Too long
        assert not validate_session_id("session with spaces")
        assert not validate_session_id("session/with/slashes")
        assert not validate_session_id("session.with.dots")
        assert not validate_session_id("session@with@symbols")

    def test_build_main_session_id(self):
        """Test main session id generation."""
        sid = build_main_session_id("run123", "ctx456")
        assert sid.startswith("main-run123-ctx456-")
        assert validate_session_id(sid)

    def test_build_subagent_session_id(self):
        """Test subagent session id generation."""
        sid = build_subagent_session_id("security-context-agent", "run123", "ctx456", "task789")
        assert sid.startswith("sub-security-context-agent-run123-task789-")
        assert validate_session_id(sid)

    def test_build_session_id_with_empty_run(self):
        """Test session id generation with empty run_id."""
        sid = build_main_session_id("", "ctx456")
        assert sid.startswith("main-no-run-ctx456-")
        assert validate_session_id(sid)


# ============================================================================
# 6.2-6.3 Path Layout Tests
# ============================================================================


class TestPathLayout:
    def test_main_session_path(self, store, temp_dir):
        """Test main session directory layout."""
        meta = MemorySessionMeta(
            session_id="main-test-123",
            run_id="run123",
            agent_kind=AgentKind.MAIN,
            agent_type="main-agent",
            context_id="ctx456",
        )
        store.create_session(meta)

        expected_dir = Path(temp_dir) / "memory" / "main" / "main-test-123"
        assert expected_dir.exists()
        assert (expected_dir / "state.json").exists()
        assert (expected_dir / "transcript.jsonl").exists()

    def test_subagent_session_path(self, store, temp_dir):
        """Test subagent session directory layout."""
        meta = MemorySessionMeta(
            session_id="sub-security-run123",
            run_id="run123",
            agent_kind=AgentKind.SUBAGENT,
            agent_type="security-context-agent",
            context_id="ctx456",
            task_id="task789",
        )
        store.create_session(meta)

        expected_dir = (
            Path(temp_dir)
            / "memory"
            / "subagents"
            / "security-context-agent"
            / "sub-security-run123"
        )
        assert expected_dir.exists()
        assert (expected_dir / "state.json").exists()
        assert (expected_dir / "transcript.jsonl").exists()

    def test_state_json_content(self, store, temp_dir):
        """Test state.json contains correct metadata."""
        meta = MemorySessionMeta(
            session_id="main-test-123",
            run_id="run123",
            agent_kind=AgentKind.MAIN,
            agent_type="main-agent",
            context_id="ctx456",
        )
        store.create_session(meta)

        state_file = Path(temp_dir) / "memory" / "main" / "main-test-123" / "state.json"
        data = json.loads(state_file.read_text())

        assert data["session_id"] == "main-test-123"
        assert data["run_id"] == "run123"
        assert data["agent_kind"] == "main"
        assert data["agent_type"] == "main-agent"
        assert data["context_id"] == "ctx456"
        assert data["message_count"] == 0


# ============================================================================
# 6.4 Session Creation Tests
# ============================================================================


class TestSessionCreation:
    def test_create_session(self, store):
        """Test explicit session creation."""
        meta = MemorySessionMeta(
            session_id="test-session",
            run_id="run123",
            agent_kind=AgentKind.MAIN,
            agent_type="main-agent",
            context_id="ctx456",
        )
        result = store.create_session(meta)
        assert result.session_id == "test-session"

    def test_create_duplicate_session(self, store):
        """Test creating duplicate session raises error."""
        meta = MemorySessionMeta(
            session_id="test-session",
            run_id="run123",
            agent_kind=AgentKind.MAIN,
            agent_type="main-agent",
            context_id="ctx456",
        )
        store.create_session(meta)

        with pytest.raises(SessionAlreadyExistsError):
            store.create_session(meta)

    def test_create_session_with_empty_id(self, store):
        """Test creating session with empty id raises error."""
        meta = MemorySessionMeta(
            session_id="",
            run_id="run123",
            agent_kind=AgentKind.MAIN,
            agent_type="main-agent",
            context_id="ctx456",
        )
        with pytest.raises(InvalidSessionError):
            store.create_session(meta)

    def test_append_to_unknown_session(self, store):
        """Test appending to unknown session raises error."""
        entry = TranscriptEntry.create(
            session_id="nonexistent",
            entry_type=EntryType.MESSAGE,
            payload={"role": "user", "content": "test"},
        )
        with pytest.raises(AppendToUnknownSessionError):
            store.append_entry(entry)


# ============================================================================
# 6.5 Transcript Entry Tests
# ============================================================================


class TestTranscriptEntries:
    def _create_session(self, store, session_id="test-session"):
        meta = MemorySessionMeta(
            session_id=session_id,
            run_id="run123",
            agent_kind=AgentKind.MAIN,
            agent_type="main-agent",
            context_id="ctx456",
        )
        return store.create_session(meta)

    def test_append_message(self, store):
        """Test appending message entry."""
        self._create_session(store)
        entry = append_message(store, "test-session", {
            "role": "user",
            "content": "Hello, world!",
        })

        assert entry.type == EntryType.MESSAGE
        assert entry.session_id == "test-session"
        assert entry.payload["role"] == "user"
        assert entry.payload["content"] == "Hello, world!"

    def test_append_event(self, store):
        """Test appending event entry."""
        self._create_session(store)
        entry = append_event(store, "test-session", {
            "event_type": "run.started",
            "context_id": "ctx456",
        })

        assert entry.type == EntryType.AGENT_EVENT
        assert entry.payload["event_type"] == "run.started"

    def test_append_summary(self, store):
        """Test appending summary entry."""
        self._create_session(store)
        entry = append_summary(store, "test-session", {
            "summary_text": "This is a summary of previous messages.",
        })

        assert entry.type == EntryType.SUMMARY
        assert entry.payload["summary_text"] == "This is a summary of previous messages."

    def test_append_todo_state(self, store):
        """Test appending todo state entry."""
        self._create_session(store)
        todos = [
            {"content": "Task 1", "status": "completed"},
            {"content": "Task 2", "status": "in_progress"},
        ]
        entry = append_todo_state(store, "test-session", todos)

        assert entry.type == EntryType.TODO_STATE
        assert entry.payload["todos"] == todos

    def test_append_evidence_package(self, store):
        """Test appending evidence package entry."""
        self._create_session(store)
        package = {
            "task_id": "task123",
            "task_type": "security_context",
            "status": "found_context",
            "findings": [],
        }
        entry = append_evidence_package(store, "test-session", package)

        assert entry.type == EntryType.EVIDENCE_PACKAGE
        assert entry.payload["task_id"] == "task123"

    def test_load_entries(self, store):
        """Test loading all entries from a session."""
        self._create_session(store)

        # Append multiple entries
        append_message(store, "test-session", {"role": "user", "content": "msg1"})
        append_event(store, "test-session", {"event_type": "test"})
        append_message(store, "test-session", {"role": "assistant", "content": "msg2"})

        entries = store.load_entries("test-session")
        assert len(entries) == 3
        assert entries[0].type == EntryType.MESSAGE
        assert entries[1].type == EntryType.AGENT_EVENT
        assert entries[2].type == EntryType.MESSAGE

    def test_counter_updates(self, store):
        """Test that counters are updated correctly."""
        self._create_session(store)

        append_message(store, "test-session", {"role": "user", "content": "msg1"})
        append_message(store, "test-session", {"role": "assistant", "content": "msg2"})
        append_event(store, "test-session", {"event_type": "test"})
        append_summary(store, "test-session", {"summary_text": "summary"})

        meta = store.get_session("test-session")
        assert meta.message_count == 2
        assert meta.event_count == 1
        assert meta.summary_count == 1
        assert meta.evidence_count == 0


# ============================================================================
# 6.6-6.7 Hydration Tests
# ============================================================================


class TestHydration:
    def _create_session(self, store, session_id="test-session"):
        meta = MemorySessionMeta(
            session_id=session_id,
            run_id="run123",
            agent_kind=AgentKind.MAIN,
            agent_type="main-agent",
            context_id="ctx456",
        )
        return store.create_session(meta)

    def test_hydrate_normal_messages(self, store):
        """Test hydrating normal messages."""
        self._create_session(store)

        append_message(store, "test-session", {"role": "system", "content": "You are helpful."})
        append_message(store, "test-session", {"role": "user", "content": "Hello!"})
        append_message(store, "test-session", {"role": "assistant", "content": "Hi there!"})

        messages = hydrate_messages(store, "test-session")
        assert len(messages) == 3
        assert messages[0].role.value == "system"
        assert messages[1].role.value == "user"
        assert messages[2].role.value == "assistant"

    def test_hydrate_ignores_non_model_entries(self, store):
        """Test that non-model entries are ignored during hydration."""
        self._create_session(store)

        append_message(store, "test-session", {"role": "user", "content": "Hello!"})
        append_event(store, "test-session", {"event_type": "test"})
        append_todo_state(store, "test-session", [{"content": "task", "status": "done"}])
        append_message(store, "test-session", {"role": "assistant", "content": "Hi!"})

        messages = hydrate_messages(store, "test-session")
        assert len(messages) == 2  # Only messages, not events/todos

    def test_hydrate_with_summary_boundary(self, store):
        """Test hydration with summary boundary."""
        self._create_session(store)

        # Old messages
        append_message(store, "test-session", {"role": "user", "content": "Old message 1"})
        append_message(store, "test-session", {"role": "assistant", "content": "Old response 1"})

        # Summary
        append_summary(store, "test-session", {
            "summary_text": "Summary of old conversation.",
        })

        # Recent messages
        append_message(store, "test-session", {"role": "user", "content": "New message"})
        append_message(store, "test-session", {"role": "assistant", "content": "New response"})

        messages = hydrate_messages(store, "test-session")
        # Should have summary + recent messages
        assert len(messages) >= 1
        # Summary should be present
        summary_found = any("Summary of old conversation" in str(m.content) for m in messages)
        assert summary_found

    def test_hydrate_empty_session(self, store):
        """Test hydrating empty session."""
        self._create_session(store)
        messages = hydrate_messages(store, "test-session")
        assert messages == []

    def test_hydrate_nonexistent_session(self, store):
        """Test hydrating nonexistent session raises error."""
        with pytest.raises(SessionNotFoundError):
            hydrate_messages(store, "nonexistent")


# ============================================================================
# 6.8 Session Isolation Tests
# ============================================================================


class TestSessionIsolation:
    def test_main_and_subagent_isolated(self, store):
        """Test that main and subagent sessions are isolated."""
        # Create main session
        main_meta = MemorySessionMeta(
            session_id="main-session",
            run_id="run123",
            agent_kind=AgentKind.MAIN,
            agent_type="main-agent",
            context_id="ctx456",
        )
        store.create_session(main_meta)

        # Create subagent session
        sub_meta = MemorySessionMeta(
            session_id="sub-session",
            run_id="run123",
            agent_kind=AgentKind.SUBAGENT,
            agent_type="security-context-agent",
            context_id="ctx456",
            task_id="task789",
        )
        store.create_session(sub_meta)

        # Append to main
        append_message(store, "main-session", {"role": "user", "content": "main message"})

        # Append to subagent
        append_message(store, "sub-session", {"role": "user", "content": "sub message"})

        # Verify isolation
        main_entries = store.load_entries("main-session")
        sub_entries = store.load_entries("sub-session")

        assert len(main_entries) == 1
        assert len(sub_entries) == 1
        assert main_entries[0].payload["content"] == "main message"
        assert sub_entries[0].payload["content"] == "sub message"

    def test_multiple_subagents_isolated(self, store):
        """Test that multiple subagent sessions are isolated."""
        # Create subagent sessions
        for i, agent_type in enumerate(["security-context-agent", "test-context-agent"]):
            meta = MemorySessionMeta(
                session_id=f"sub-{i}",
                run_id="run123",
                agent_kind=AgentKind.SUBAGENT,
                agent_type=agent_type,
                context_id="ctx456",
                task_id=f"task{i}",
            )
            store.create_session(meta)
            append_message(store, f"sub-{i}", {"role": "user", "content": f"message {i}"})

        # Verify isolation
        entries_0 = store.load_entries("sub-0")
        entries_1 = store.load_entries("sub-1")

        assert len(entries_0) == 1
        assert len(entries_1) == 1
        assert entries_0[0].payload["content"] == "message 0"
        assert entries_1[0].payload["content"] == "message 1"


# ============================================================================
# 6.9-6.10 Integration Tests
# ============================================================================


class TestIntegration:
    def test_list_sessions(self, store):
        """Test listing sessions with filters."""
        # Create sessions
        store.create_session(MemorySessionMeta(
            session_id="main-1",
            run_id="run123",
            agent_kind=AgentKind.MAIN,
            agent_type="main-agent",
            context_id="ctx456",
        ))
        store.create_session(MemorySessionMeta(
            session_id="sub-1",
            run_id="run123",
            agent_kind=AgentKind.SUBAGENT,
            agent_type="security-context-agent",
            context_id="ctx456",
        ))
        store.create_session(MemorySessionMeta(
            session_id="sub-2",
            run_id="run123",
            agent_kind=AgentKind.SUBAGENT,
            agent_type="test-context-agent",
            context_id="ctx456",
        ))

        # List all
        all_sessions = store.list_sessions()
        assert len(all_sessions) == 3

        # List by kind
        main_sessions = store.list_sessions(agent_kind=AgentKind.MAIN)
        assert len(main_sessions) == 1
        assert main_sessions[0].session_id == "main-1"

        sub_sessions = store.list_sessions(agent_kind=AgentKind.SUBAGENT)
        assert len(sub_sessions) == 2

        # List by type
        security_sessions = store.list_sessions(agent_type="security-context-agent")
        assert len(security_sessions) == 1
        assert security_sessions[0].session_id == "sub-1"

    def test_full_workflow(self, store):
        """Test full workflow: create, append, hydrate."""
        # Create session
        meta = MemorySessionMeta(
            session_id="workflow-test",
            run_id="run123",
            agent_kind=AgentKind.MAIN,
            agent_type="main-agent",
            context_id="ctx456",
        )
        store.create_session(meta)

        # Append messages
        append_message(store, "workflow-test", {"role": "system", "content": "You are helpful."})
        append_message(store, "workflow-test", {"role": "user", "content": "Review this PR."})
        append_message(store, "workflow-test", {"role": "assistant", "content": "I'll review it."})

        # Append event
        append_event(store, "workflow-test", {"event_type": "run.completed"})

        # Append evidence
        append_evidence_package(store, "workflow-test", {
            "task_id": "task1",
            "status": "found_context",
        })

        # Hydrate (should only get messages)
        messages = hydrate_messages(store, "workflow-test")
        assert len(messages) == 3

        # Check counters
        meta = store.get_session("workflow-test")
        assert meta.message_count == 3
        assert meta.event_count == 1
        assert meta.evidence_count == 1
