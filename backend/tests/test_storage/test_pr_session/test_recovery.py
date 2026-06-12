"""Tests for startup recovery: terminal runs, interrupted runs,
restored contexts, event replay, and idempotent cancellation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.agent.runtime.events import AgentRunSession, RunEvent, RunStatus
from backend.agent.runtime.run_manager import RunManager
from backend.storage.pr_session.models import (
    PersistedEvent,
    PRSessionMeta,
    ResultRecord,
    RunLifecycle,
    RunState,
)
from backend.storage.pr_session.recovery import (
    RecoveryReport,
    create_retry_run,
    recover_on_startup,
)
from backend.storage.pr_session.store import FilePRSessionStore


@pytest.fixture
def store(tmp_path: Path) -> FilePRSessionStore:
    return FilePRSessionStore(tmp_path / "storage")


@pytest.fixture
def run_manager() -> RunManager:
    return RunManager()


# ---------------------------------------------------------------------------
# Terminal run recovery
# ---------------------------------------------------------------------------


class TestTerminalRunRecovery:
    def test_recover_completed_run(
        self, store: FilePRSessionStore, run_manager: RunManager
    ):
        meta = store.get_or_create_pr_session("octocat", "hello", 42)
        rs = store.create_run(meta.pr_session_id, "ctx-1", "aaa", "bbb")
        store.update_run_state(rs.run_id, lifecycle=RunLifecycle.COMPLETED)
        store.save_result(ResultRecord(
            run_id=rs.run_id, pr_session_id=meta.pr_session_id,
            lifecycle="completed",
            findings=[{"title": "Bug", "severity": "critical"}],
        ))

        # Persist some events
        for i in range(3):
            store.append_event(PersistedEvent(
                event_id=f"e{i}", run_id=rs.run_id,
                sequence=i, event_type="tool.call", created_at=f"t{i}",
            ))
        store.append_event(PersistedEvent(
            event_id="e-done", run_id=rs.run_id,
            sequence=3, event_type="run.completed", created_at="t3",
        ))

        report = recover_on_startup(store, run_manager)

        assert report.terminal_runs_restored == 1
        assert run_manager.has_run(rs.run_id)
        session = run_manager.get_run(rs.run_id)
        assert session.status == RunStatus.COMPLETED
        assert session.final_result is not None

    def test_recover_failed_run(
        self, store: FilePRSessionStore, run_manager: RunManager
    ):
        meta = store.get_or_create_pr_session("octocat", "hello", 42)
        rs = store.create_run(meta.pr_session_id, "ctx-1", "aaa", "bbb")
        store.update_run_state(
            rs.run_id,
            lifecycle=RunLifecycle.FAILED,
            error_summary="timeout",
        )

        report = recover_on_startup(store, run_manager)
        assert report.terminal_runs_restored == 1
        session = run_manager.get_run(rs.run_id)
        assert session.status == RunStatus.FAILED
        assert session.error_summary == "timeout"

    def test_recover_cancelled_run(
        self, store: FilePRSessionStore, run_manager: RunManager
    ):
        meta = store.get_or_create_pr_session("octocat", "hello", 42)
        rs = store.create_run(meta.pr_session_id, "ctx-1", "aaa", "bbb")
        store.update_run_state(rs.run_id, lifecycle=RunLifecycle.CANCELLED)

        report = recover_on_startup(store, run_manager)
        assert report.terminal_runs_restored == 1
        session = run_manager.get_run(rs.run_id)
        assert session.status == RunStatus.CANCELLED


# ---------------------------------------------------------------------------
# Interrupted run recovery
# ---------------------------------------------------------------------------


class TestInterruptedRunRecovery:
    def test_queued_run_becomes_interrupted(
        self, store: FilePRSessionStore, run_manager: RunManager
    ):
        meta = store.get_or_create_pr_session("octocat", "hello", 42)
        rs = store.create_run(meta.pr_session_id, "ctx-1", "aaa", "bbb")
        # Run is created as QUEUED by default

        report = recover_on_startup(store, run_manager)
        assert report.interrupted_runs == 1
        assert report.terminal_runs_restored == 0

        # Verify it was marked interrupted on disk
        state = store.get_run_state(rs.run_id)
        assert state.lifecycle == RunLifecycle.INTERRUPTED

    def test_running_run_becomes_interrupted(
        self, store: FilePRSessionStore, run_manager: RunManager
    ):
        meta = store.get_or_create_pr_session("octocat", "hello", 42)
        rs = store.create_run(meta.pr_session_id, "ctx-1", "aaa", "bbb")
        store.update_run_state(rs.run_id, lifecycle=RunLifecycle.RUNNING)

        report = recover_on_startup(store, run_manager)
        assert report.interrupted_runs == 1

        state = store.get_run_state(rs.run_id)
        assert state.lifecycle == RunLifecycle.INTERRUPTED
        assert state.completed_at is not None

    def test_cancelling_run_becomes_interrupted(
        self, store: FilePRSessionStore, run_manager: RunManager
    ):
        meta = store.get_or_create_pr_session("octocat", "hello", 42)
        rs = store.create_run(meta.pr_session_id, "ctx-1", "aaa", "bbb")
        store.update_run_state(rs.run_id, lifecycle=RunLifecycle.CANCELLING)

        report = recover_on_startup(store, run_manager)
        assert report.interrupted_runs == 1

        state = store.get_run_state(rs.run_id)
        assert state.lifecycle == RunLifecycle.INTERRUPTED

    def test_interrupted_run_not_restarted(
        self, store: FilePRSessionStore, run_manager: RunManager
    ):
        """Recovery should NOT create execution tasks for interrupted runs."""
        meta = store.get_or_create_pr_session("octocat", "hello", 42)
        rs = store.create_run(meta.pr_session_id, "ctx-1", "aaa", "bbb")

        recover_on_startup(store, run_manager)

        # No execution task should be registered
        task = run_manager.get_execution_task(rs.run_id)
        assert task is None


# ---------------------------------------------------------------------------
# Event replay
# ---------------------------------------------------------------------------


class TestEventReplay:
    def test_retained_events_restored(
        self, store: FilePRSessionStore, run_manager: RunManager
    ):
        meta = store.get_or_create_pr_session("octocat", "hello", 42)
        rs = store.create_run(meta.pr_session_id, "ctx-1", "aaa", "bbb")
        store.update_run_state(rs.run_id, lifecycle=RunLifecycle.COMPLETED)

        for i in range(5):
            store.append_event(PersistedEvent(
                event_id=f"e{i}", run_id=rs.run_id,
                sequence=i, event_type="tool.call", created_at=f"t{i}",
            ))
        store.append_event(PersistedEvent(
            event_id="e-done", run_id=rs.run_id,
            sequence=5, event_type="run.completed", created_at="t5",
        ))

        recover_on_startup(store, run_manager)

        session = run_manager.get_run(rs.run_id)
        assert len(session.retained_events) == 6
        assert session.retained_events[-1].type == "run.completed"

    def test_next_sequence_restored(
        self, store: FilePRSessionStore, run_manager: RunManager
    ):
        meta = store.get_or_create_pr_session("octocat", "hello", 42)
        rs = store.create_run(meta.pr_session_id, "ctx-1", "aaa", "bbb")
        store.update_run_state(rs.run_id, lifecycle=RunLifecycle.COMPLETED)

        for i in range(3):
            store.append_event(PersistedEvent(
                event_id=f"e{i}", run_id=rs.run_id,
                sequence=i, event_type="tool.call", created_at=f"t{i}",
            ))

        recover_on_startup(store, run_manager)

        session = run_manager.get_run(rs.run_id)
        assert session._next_sequence == 3


# ---------------------------------------------------------------------------
# Multiple PR sessions
# ---------------------------------------------------------------------------


class TestMultiplePRSessions:
    def test_recover_multiple_sessions(
        self, store: FilePRSessionStore, run_manager: RunManager
    ):
        m1 = store.get_or_create_pr_session("octocat", "hello", 1)
        m2 = store.get_or_create_pr_session("octocat", "hello", 2)

        r1 = store.create_run(m1.pr_session_id, "ctx-1", "a", "b")
        r2 = store.create_run(m2.pr_session_id, "ctx-2", "c", "d")

        store.update_run_state(r1.run_id, lifecycle=RunLifecycle.COMPLETED)
        store.update_run_state(r2.run_id, lifecycle=RunLifecycle.FAILED, error_summary="err")

        report = recover_on_startup(store, run_manager)
        assert report.pr_sessions_scanned == 2
        assert report.terminal_runs_restored == 2


# ---------------------------------------------------------------------------
# Corrupt data tolerance
# ---------------------------------------------------------------------------


class TestCorruptDataTolerance:
    def test_corrupt_run_json_skipped(
        self, store: FilePRSessionStore, run_manager: RunManager
    ):
        meta = store.get_or_create_pr_session("octocat", "hello", 42)
        rs = store.create_run(meta.pr_session_id, "ctx-1", "aaa", "bbb")

        # Corrupt the run.json
        from backend.storage.pr_session.paths import run_state_file
        rsf = run_state_file(store._storage_dir, meta.pr_key, rs.run_id)
        rsf.write_text("NOT VALID JSON", encoding="utf-8")

        report = recover_on_startup(store, run_manager)
        assert report.corrupt_runs_skipped == 1
        assert report.terminal_runs_restored == 0
        assert report.interrupted_runs == 0

    def test_corrupt_pr_json_skipped(
        self, store: FilePRSessionStore, run_manager: RunManager
    ):
        meta = store.get_or_create_pr_session("octocat", "hello", 42)

        # Corrupt pr.json
        from backend.storage.pr_session.paths import pr_meta_file
        pmf = pr_meta_file(store._storage_dir, meta.pr_key)
        pmf.write_text("{bad json", encoding="utf-8")

        report = recover_on_startup(store, run_manager)
        assert report.pr_sessions_scanned == 0


# ---------------------------------------------------------------------------
# Empty storage
# ---------------------------------------------------------------------------


class TestEmptyStorage:
    def test_no_sessions_directory(
        self, store: FilePRSessionStore, run_manager: RunManager
    ):
        report = recover_on_startup(store, run_manager)
        assert report.pr_sessions_scanned == 0
        assert report.runs_scanned == 0


# ---------------------------------------------------------------------------
# Retry support
# ---------------------------------------------------------------------------


class TestRetrySupport:
    def test_create_retry_run(self, store: FilePRSessionStore):
        meta = store.get_or_create_pr_session("octocat", "hello", 42)
        rs = store.create_run(meta.pr_session_id, "ctx-1", "aaa", "bbb")
        store.update_run_state(
            rs.run_id, lifecycle=RunLifecycle.INTERRUPTED, completed_at="t",
        )

        new_rs = create_retry_run(store, rs.run_id, "ctx-1")
        assert new_rs is not None
        assert new_rs.retry_of_run_id == rs.run_id
        assert new_rs.base_sha == "aaa"
        assert new_rs.head_sha == "bbb"
        assert new_rs.lifecycle == RunLifecycle.QUEUED

    def test_retry_completed_run_returns_none(self, store: FilePRSessionStore):
        meta = store.get_or_create_pr_session("octocat", "hello", 42)
        rs = store.create_run(meta.pr_session_id, "ctx-1", "aaa", "bbb")
        store.update_run_state(rs.run_id, lifecycle=RunLifecycle.COMPLETED)

        assert create_retry_run(store, rs.run_id, "ctx-1") is None

    def test_retry_nonexistent_run(self, store: FilePRSessionStore):
        assert create_retry_run(store, "nonexistent", "ctx-1") is None
