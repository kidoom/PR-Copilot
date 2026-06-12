"""Tests for agent memory session references.

Covers: main/subagent isolation, sibling registration,
reference lookup, and missing-memory tolerance.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.storage.pr_session.models import (
    AgentSessionRef,
    AgentSessionsRecord,
    AgentSessionStatus,
    PRSessionMeta,
    RunState,
)
from backend.storage.pr_session.store import FilePRSessionStore


@pytest.fixture
def store(tmp_path: Path) -> FilePRSessionStore:
    return FilePRSessionStore(tmp_path / "storage")


@pytest.fixture
def run(store: FilePRSessionStore) -> tuple[PRSessionMeta, RunState]:
    meta = store.get_or_create_pr_session("octocat", "hello-world", 42)
    rs = store.create_run(meta.pr_session_id, "ctx-1", "aaa", "bbb")
    return meta, rs


# ---------------------------------------------------------------------------
# Main agent registration
# ---------------------------------------------------------------------------


class TestMainAgentRegistration:
    def test_register_main_session(self, store: FilePRSessionStore, run: tuple):
        pr_meta, rs = run
        record = AgentSessionsRecord(
            run_id=rs.run_id,
            sessions=[
                AgentSessionRef(
                    memory_session_id="main-abc",
                    agent_kind="main",
                    agent_type="main-agent",
                    status=AgentSessionStatus.ACTIVE,
                ),
            ],
        )
        store.save_agent_sessions(record)

        loaded = store.load_agent_sessions(rs.run_id)
        assert loaded is not None
        assert len(loaded.sessions) == 1
        assert loaded.sessions[0].agent_kind == "main"
        assert loaded.sessions[0].status == AgentSessionStatus.ACTIVE

    def test_update_main_session_status(self, store: FilePRSessionStore, run: tuple):
        pr_meta, rs = run
        record = AgentSessionsRecord(
            run_id=rs.run_id,
            sessions=[
                AgentSessionRef(
                    memory_session_id="main-abc",
                    agent_kind="main",
                    agent_type="main-agent",
                    status=AgentSessionStatus.ACTIVE,
                ),
            ],
        )
        store.save_agent_sessions(record)

        # Update status
        loaded = store.load_agent_sessions(rs.run_id)
        loaded.sessions[0].status = AgentSessionStatus.COMPLETED
        loaded.sessions[0].completed_at = "2026-01-01T01:00:00+00:00"
        store.save_agent_sessions(loaded)

        reloaded = store.load_agent_sessions(rs.run_id)
        assert reloaded.sessions[0].status == AgentSessionStatus.COMPLETED
        assert reloaded.sessions[0].completed_at is not None


# ---------------------------------------------------------------------------
# Subagent registration
# ---------------------------------------------------------------------------


class TestSubagentRegistration:
    def test_register_subagent_session(self, store: FilePRSessionStore, run: tuple):
        pr_meta, rs = run
        record = AgentSessionsRecord(
            run_id=rs.run_id,
            sessions=[
                AgentSessionRef(
                    memory_session_id="sub-sec-001",
                    agent_kind="subagent",
                    agent_type="security_context",
                    task_id="task-1",
                    child_session_id="child-001",
                    status=AgentSessionStatus.ACTIVE,
                ),
            ],
        )
        store.save_agent_sessions(record)

        loaded = store.load_agent_sessions(rs.run_id)
        assert loaded.sessions[0].agent_kind == "subagent"
        assert loaded.sessions[0].task_id == "task-1"
        assert loaded.sessions[0].child_session_id == "child-001"


# ---------------------------------------------------------------------------
# Sibling registration (main + multiple subagents)
# ---------------------------------------------------------------------------


class TestSiblingRegistration:
    def test_main_and_subagents(self, store: FilePRSessionStore, run: tuple):
        pr_meta, rs = run
        record = AgentSessionsRecord(
            run_id=rs.run_id,
            sessions=[
                AgentSessionRef(
                    memory_session_id="main-abc",
                    agent_kind="main",
                    agent_type="main-agent",
                    status=AgentSessionStatus.ACTIVE,
                ),
                AgentSessionRef(
                    memory_session_id="sub-sec-001",
                    agent_kind="subagent",
                    agent_type="security_context",
                    task_id="task-1",
                    child_session_id="child-001",
                    status=AgentSessionStatus.ACTIVE,
                ),
                AgentSessionRef(
                    memory_session_id="sub-test-002",
                    agent_kind="subagent",
                    agent_type="test_context",
                    task_id="task-2",
                    child_session_id="child-002",
                    status=AgentSessionStatus.ACTIVE,
                ),
            ],
        )
        store.save_agent_sessions(record)

        loaded = store.load_agent_sessions(rs.run_id)
        assert len(loaded.sessions) == 3
        kinds = {s.agent_kind for s in loaded.sessions}
        assert "main" in kinds
        assert "subagent" in kinds

    def test_update_individual_subagent_status(self, store: FilePRSessionStore, run: tuple):
        pr_meta, rs = run
        record = AgentSessionsRecord(
            run_id=rs.run_id,
            sessions=[
                AgentSessionRef(
                    memory_session_id="main-abc",
                    agent_kind="main",
                    agent_type="main-agent",
                    status=AgentSessionStatus.ACTIVE,
                ),
                AgentSessionRef(
                    memory_session_id="sub-sec-001",
                    agent_kind="subagent",
                    agent_type="security_context",
                    task_id="task-1",
                    child_session_id="child-001",
                    status=AgentSessionStatus.ACTIVE,
                ),
            ],
        )
        store.save_agent_sessions(record)

        # Update only the subagent
        loaded = store.load_agent_sessions(rs.run_id)
        for ref in loaded.sessions:
            if ref.agent_kind == "subagent":
                ref.status = AgentSessionStatus.COMPLETED
                ref.completed_at = "2026-01-01T01:00:00+00:00"
        store.save_agent_sessions(loaded)

        reloaded = store.load_agent_sessions(rs.run_id)
        main_ref = next(s for s in reloaded.sessions if s.agent_kind == "main")
        sub_ref = next(s for s in reloaded.sessions if s.agent_kind == "subagent")
        assert main_ref.status == AgentSessionStatus.ACTIVE
        assert sub_ref.status == AgentSessionStatus.COMPLETED


# ---------------------------------------------------------------------------
# Reference lookup
# ---------------------------------------------------------------------------


class TestReferenceLookup:
    def test_load_agent_sessions_returns_none_when_missing(
        self, store: FilePRSessionStore, run: tuple
    ):
        pr_meta, rs = run
        assert store.load_agent_sessions(rs.run_id) is None

    def test_load_agent_sessions_for_nonexistent_run(self, store: FilePRSessionStore):
        assert store.load_agent_sessions("nonexistent") is None


# ---------------------------------------------------------------------------
# Missing-memory tolerance
# ---------------------------------------------------------------------------


class TestMissingMemoryTolerance:
    def test_run_survives_missing_agent_sessions(
        self, store: FilePRSessionStore, run: tuple
    ):
        """The run and its result should remain available even if
        agent-sessions.json is missing or corrupt."""
        pr_meta, rs = run

        # Don't write agent-sessions.json at all
        loaded = store.load_agent_sessions(rs.run_id)
        assert loaded is None

        # Run state should still be accessible
        state = store.get_run_state(rs.run_id)
        assert state is not None

    def test_corrupt_agent_sessions_returns_none(
        self, store: FilePRSessionStore, run: tuple
    ):
        pr_meta, rs = run

        # Write corrupt JSON
        from backend.storage.pr_session.paths import run_agent_sessions_file
        asf = run_agent_sessions_file(store._storage_dir, pr_meta.pr_key, rs.run_id)
        asf.parent.mkdir(parents=True, exist_ok=True)
        asf.write_text("NOT VALID JSON", encoding="utf-8")

        loaded = store.load_agent_sessions(rs.run_id)
        assert loaded is None


# ---------------------------------------------------------------------------
# All status values
# ---------------------------------------------------------------------------


class TestAgentSessionStatusValues:
    def test_all_statuses_roundtrip(self, store: FilePRSessionStore, run: tuple):
        pr_meta, rs = run
        statuses = [
            AgentSessionStatus.ACTIVE,
            AgentSessionStatus.COMPLETED,
            AgentSessionStatus.FAILED,
            AgentSessionStatus.CANCELLED,
            AgentSessionStatus.INVALID,
            AgentSessionStatus.MAX_STEP,
        ]
        record = AgentSessionsRecord(
            run_id=rs.run_id,
            sessions=[
                AgentSessionRef(
                    memory_session_id=f"sess-{i}",
                    agent_kind="main",
                    agent_type="test",
                    status=status,
                )
                for i, status in enumerate(statuses)
            ],
        )
        store.save_agent_sessions(record)

        loaded = store.load_agent_sessions(rs.run_id)
        assert len(loaded.sessions) == 6
        loaded_statuses = [s.status for s in loaded.sessions]
        assert loaded_statuses == statuses
