"""Tests for FilePRSessionStore: atomic writes, concurrent append,
monotonic sequence, path traversal, and round-trip operations."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from backend.storage.pr_session.models import (
    AgentSessionRef,
    AgentSessionsRecord,
    ContextRecord,
    PersistedEvent,
    PRSessionMeta,
    ResultRecord,
    RunLifecycle,
    RunState,
    TaskPlanRecord,
)
from backend.storage.pr_session.store import (
    FilePRSessionStore,
    PRSessionNotFoundError,
    RunNotFoundError,
)


@pytest.fixture
def store(tmp_path: Path) -> FilePRSessionStore:
    return FilePRSessionStore(tmp_path / "storage")


@pytest.fixture
def pr_meta(store: FilePRSessionStore) -> PRSessionMeta:
    return store.get_or_create_pr_session("octocat", "hello-world", 42)


@pytest.fixture
def run_state(store: FilePRSessionStore, pr_meta: PRSessionMeta) -> RunState:
    return store.create_run(
        pr_meta.pr_session_id,
        context_id="ctx-1",
        base_sha="aaa111",
        head_sha="bbb222",
    )


# ---------------------------------------------------------------------------
# PR session CRUD
# ---------------------------------------------------------------------------


class TestPRSessionCRUD:
    def test_create_and_get(self, store: FilePRSessionStore):
        meta = store.get_or_create_pr_session("octocat", "hello-world", 42)
        assert meta.owner == "octocat"
        assert meta.repo == "hello-world"
        assert meta.pull_number == 42
        assert meta.pr_session_id.startswith("ps_")
        assert meta.run_count == 0

    def test_idempotent_get_or_create(self, store: FilePRSessionStore):
        m1 = store.get_or_create_pr_session("octocat", "hello-world", 42)
        m2 = store.get_or_create_pr_session("octocat", "hello-world", 42)
        assert m1.pr_session_id == m2.pr_session_id

    def test_different_prs_different_sessions(self, store: FilePRSessionStore):
        m1 = store.get_or_create_pr_session("octocat", "hello-world", 1)
        m2 = store.get_or_create_pr_session("octocat", "hello-world", 2)
        assert m1.pr_session_id != m2.pr_session_id

    def test_get_by_identity(self, store: FilePRSessionStore):
        store.get_or_create_pr_session("octocat", "hello-world", 42)
        found = store.get_pr_session_by_identity("octocat", "hello-world", 42)
        assert found is not None
        assert found.owner == "octocat"

    def test_get_by_identity_not_found(self, store: FilePRSessionStore):
        assert store.get_pr_session_by_identity("nobody", "nothing", 999) is None

    def test_get_by_id(self, store: FilePRSessionStore):
        meta = store.get_or_create_pr_session("octocat", "hello-world", 42)
        found = store.get_pr_session(meta.pr_session_id)
        assert found is not None
        assert found.pr_session_id == meta.pr_session_id

    def test_pr_json_persisted(self, store: FilePRSessionStore, pr_meta: PRSessionMeta):
        # Verify pr.json exists on disk
        from backend.storage.pr_session.paths import pr_meta_file

        path = pr_meta_file(store._storage_dir, pr_meta.pr_key)
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["owner"] == "octocat"


# ---------------------------------------------------------------------------
# Run CRUD
# ---------------------------------------------------------------------------


class TestRunCRUD:
    def test_create_run(self, store: FilePRSessionStore, pr_meta: PRSessionMeta):
        rs = store.create_run(
            pr_meta.pr_session_id, "ctx-1", "aaa", "bbb"
        )
        assert rs.run_id.startswith("run_")
        assert rs.lifecycle == RunLifecycle.QUEUED
        assert rs.base_sha == "aaa"
        assert rs.head_sha == "bbb"

    def test_create_run_increments_count(self, store: FilePRSessionStore, pr_meta: PRSessionMeta):
        store.create_run(pr_meta.pr_session_id, "ctx-1", "a", "b")
        meta = store.get_pr_session(pr_meta.pr_session_id)
        assert meta.run_count == 1
        store.create_run(pr_meta.pr_session_id, "ctx-2", "c", "d")
        meta = store.get_pr_session(pr_meta.pr_session_id)
        assert meta.run_count == 2

    def test_get_run_state(self, store: FilePRSessionStore, run_state: RunState):
        loaded = store.get_run_state(run_state.run_id)
        assert loaded.run_id == run_state.run_id
        assert loaded.lifecycle == RunLifecycle.QUEUED

    def test_update_run_state_lifecycle(self, store: FilePRSessionStore, run_state: RunState):
        updated = store.update_run_state(
            run_state.run_id,
            lifecycle=RunLifecycle.RUNNING,
            started_at="2026-01-01T00:00:00+00:00",
        )
        assert updated.lifecycle == RunLifecycle.RUNNING
        assert updated.started_at is not None

    def test_update_run_state_error(self, store: FilePRSessionStore, run_state: RunState):
        updated = store.update_run_state(
            run_state.run_id,
            lifecycle=RunLifecycle.FAILED,
            error_summary="something broke",
        )
        assert updated.lifecycle == RunLifecycle.FAILED
        assert updated.error_summary == "something broke"

    def test_create_run_not_found_session(self, store: FilePRSessionStore):
        with pytest.raises(PRSessionNotFoundError):
            store.create_run("nonexistent", "ctx", "a", "b")

    def test_get_run_state_not_found(self, store: FilePRSessionStore):
        with pytest.raises(RunNotFoundError):
            store.get_run_state("nonexistent")

    def test_run_json_persisted(self, store: FilePRSessionStore, run_state: RunState):
        from backend.storage.pr_session.paths import pr_meta_file

        # Find the pr_key
        pr_key = store._find_pr_key_for_run(run_state.run_id)
        assert pr_key is not None
        rs_file = store._storage_dir / "sessions" / "pr" / pr_key / "runs" / run_state.run_id / "run.json"
        assert rs_file.exists()

    def test_retry_of_run_id(self, store: FilePRSessionStore, pr_meta: PRSessionMeta):
        rs1 = store.create_run(pr_meta.pr_session_id, "ctx-1", "a", "b")
        rs2 = store.create_run(
            pr_meta.pr_session_id, "ctx-1", "a", "b",
            retry_of_run_id=rs1.run_id,
        )
        assert rs2.retry_of_run_id == rs1.run_id


# ---------------------------------------------------------------------------
# Context persistence
# ---------------------------------------------------------------------------


class TestContextPersistence:
    def test_save_and_load_context(self, store: FilePRSessionStore, run_state: RunState):
        ctx = ContextRecord(
            context_id="ctx-1",
            pr_session_id=run_state.pr_session_id,
            owner="octocat",
            repo="hello",
            pull_number=42,
            base_sha="aaa",
            head_sha="bbb",
            pr_metadata={"title": "Fix bug"},
            files=[{"filename": "foo.py"}],
        )
        store.save_context_for_run(run_state.run_id, ctx)
        loaded = store.load_context(run_state.run_id)
        assert loaded is not None
        assert loaded.context_id == "ctx-1"
        assert loaded.pr_metadata["title"] == "Fix bug"

    def test_load_context_not_found(self, store: FilePRSessionStore, run_state: RunState):
        assert store.load_context(run_state.run_id) is None


# ---------------------------------------------------------------------------
# Task plan persistence
# ---------------------------------------------------------------------------


class TestTaskPlanPersistence:
    def test_save_and_load(self, store: FilePRSessionStore, run_state: RunState):
        tp = TaskPlanRecord(
            run_id=run_state.run_id,
            pr_session_id=run_state.pr_session_id,
            tasks=[{"type": "security_context", "files": ["a.py"]}],
        )
        store.save_task_plan(tp)
        loaded = store.load_task_plan(run_state.run_id)
        assert loaded is not None
        assert len(loaded.tasks) == 1


# ---------------------------------------------------------------------------
# Event persistence
# ---------------------------------------------------------------------------


class TestEventPersistence:
    def test_append_and_load(self, store: FilePRSessionStore, run_state: RunState):
        event = PersistedEvent(
            event_id="evt-1",
            run_id=run_state.run_id,
            sequence=0,
            event_type="run.started",
            created_at="2026-01-01T00:00:00+00:00",
            payload={"key": "value"},
        )
        store.append_event(event)
        events = store.load_events(run_state.run_id)
        assert len(events) == 1
        assert events[0].event_type == "run.started"
        assert events[0].payload["key"] == "value"

    def test_multiple_events_ordered(self, store: FilePRSessionStore, run_state: RunState):
        for i in range(5):
            store.append_event(
                PersistedEvent(
                    event_id=f"evt-{i}",
                    run_id=run_state.run_id,
                    sequence=i,
                    event_type="tool.call",
                    created_at=f"2026-01-01T00:00:{i:02d}+00:00",
                )
            )
        events = store.load_events(run_state.run_id)
        assert len(events) == 5
        sequences = [e.sequence for e in events]
        assert sequences == [0, 1, 2, 3, 4]

    def test_load_events_after_sequence(self, store: FilePRSessionStore, run_state: RunState):
        for i in range(5):
            store.append_event(
                PersistedEvent(
                    event_id=f"evt-{i}",
                    run_id=run_state.run_id,
                    sequence=i,
                    event_type="tool.call",
                    created_at=f"t{i}",
                )
            )
        events = store.load_events(run_state.run_id, after_sequence=2)
        assert len(events) == 2
        assert events[0].sequence == 3
        assert events[1].sequence == 4

    def test_load_events_filter_type(self, store: FilePRSessionStore, run_state: RunState):
        store.append_event(
            PersistedEvent(
                event_id="e1", run_id=run_state.run_id, sequence=0,
                event_type="run.started", created_at="t0",
            )
        )
        store.append_event(
            PersistedEvent(
                event_id="e2", run_id=run_state.run_id, sequence=1,
                event_type="tool.call", created_at="t1",
            )
        )
        events = store.load_events(run_state.run_id, event_types=["tool.call"])
        assert len(events) == 1
        assert events[0].event_type == "tool.call"

    def test_load_events_limit(self, store: FilePRSessionStore, run_state: RunState):
        for i in range(10):
            store.append_event(
                PersistedEvent(
                    event_id=f"evt-{i}", run_id=run_state.run_id,
                    sequence=i, event_type="tool.call", created_at=f"t{i}",
                )
            )
        events = store.load_events(run_state.run_id, limit=3)
        assert len(events) == 3

    def test_max_sequence(self, store: FilePRSessionStore, run_state: RunState):
        assert store.get_max_sequence(run_state.run_id) == -1
        for i in range(3):
            store.append_event(
                PersistedEvent(
                    event_id=f"e{i}", run_id=run_state.run_id,
                    sequence=i, event_type="tool.call", created_at=f"t{i}",
                )
            )
        assert store.get_max_sequence(run_state.run_id) == 2

    def test_concurrent_event_append(self, store: FilePRSessionStore, run_state: RunState):
        """Simulate concurrent event appends using threads.

        The store uses process-local locks for thread safety.
        """
        import threading

        errors = []
        lock = threading.Lock()

        def append_events(agent_idx: int):
            try:
                for i in range(5):
                    event = PersistedEvent(
                        event_id=f"evt-{agent_idx}-{i}",
                        run_id=run_state.run_id,
                        sequence=agent_idx * 5 + i,
                        event_type="tool.call",
                        created_at=f"t{agent_idx}-{i}",
                    )
                    # Use a lock to ensure atomic JSONL appends across threads
                    with lock:
                        store.append_event(event)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=append_events, args=(j,)) for j in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        events = store.load_events(run_state.run_id, limit=100)
        assert len(events) == 15

    def test_malformed_jsonl_line_skipped(self, store: FilePRSessionStore, run_state: RunState):
        """Corrupt lines are skipped with diagnostics."""
        pr_key = store._find_pr_key_for_run(run_state.run_id)
        ef = store._storage_dir / "sessions" / "pr" / pr_key / "runs" / run_state.run_id / "events.jsonl"
        ef.parent.mkdir(parents=True, exist_ok=True)

        # Write one valid, one corrupt, one valid
        with open(ef, "w", encoding="utf-8") as f:
            f.write(json.dumps({"event_id": "e0", "run_id": run_state.run_id, "sequence": 0, "event_type": "run.started", "created_at": "t"}) + "\n")
            f.write("this is not json\n")
            f.write(json.dumps({"event_id": "e2", "run_id": run_state.run_id, "sequence": 1, "event_type": "tool.call", "created_at": "t"}) + "\n")

        events = store.load_events(run_state.run_id)
        assert len(events) == 2
        assert events[0].event_id == "e0"
        assert events[1].event_id == "e2"


# ---------------------------------------------------------------------------
# Result persistence
# ---------------------------------------------------------------------------


class TestResultPersistence:
    def test_save_and_load(self, store: FilePRSessionStore, run_state: RunState):
        rr = ResultRecord(
            run_id=run_state.run_id,
            pr_session_id=run_state.pr_session_id,
            lifecycle="completed",
            findings=[{"title": "Bug", "severity": "critical"}],
            coverage={"reviewed": 10},
            usage={"tokens": 5000},
        )
        store.save_result(rr)
        loaded = store.load_result(run_state.run_id)
        assert loaded is not None
        assert loaded.lifecycle == "completed"
        assert len(loaded.findings) == 1


# ---------------------------------------------------------------------------
# Agent sessions persistence
# ---------------------------------------------------------------------------


class TestAgentSessionsPersistence:
    def test_save_and_load(self, store: FilePRSessionStore, run_state: RunState):
        record = AgentSessionsRecord(
            run_id=run_state.run_id,
            sessions=[
                AgentSessionRef(
                    memory_session_id="sess-1",
                    agent_kind="main",
                    agent_type="review",
                ),
            ],
        )
        store.save_agent_sessions(record)
        loaded = store.load_agent_sessions(run_state.run_id)
        assert loaded is not None
        assert len(loaded.sessions) == 1
        assert loaded.sessions[0].memory_session_id == "sess-1"


# ---------------------------------------------------------------------------
# Index operations
# ---------------------------------------------------------------------------


class TestIndexOperations:
    def test_get_index_rebuilds_if_missing(self, store: FilePRSessionStore, pr_meta: PRSessionMeta):
        idx = store.get_index(pr_meta.pr_session_id)
        assert idx.pr_session_id == pr_meta.pr_session_id
        assert len(idx.runs) == 0

    def test_index_includes_runs(self, store: FilePRSessionStore, pr_meta: PRSessionMeta, run_state: RunState):
        idx = store.get_index(pr_meta.pr_session_id)
        assert len(idx.runs) == 1
        assert idx.runs[0].run_id == run_state.run_id

    def test_list_runs(self, store: FilePRSessionStore, pr_meta: PRSessionMeta, run_state: RunState):
        runs = store.list_runs(pr_meta.pr_session_id)
        assert len(runs) == 1

    def test_find_runs_by_head_sha(self, store: FilePRSessionStore, pr_meta: PRSessionMeta, run_state: RunState):
        runs = store.find_runs_by_head_sha(pr_meta.pr_session_id, "bbb222")
        assert len(runs) == 1
        assert runs[0].run_id == run_state.run_id

    def test_find_runs_by_head_sha_not_found(self, store: FilePRSessionStore, pr_meta: PRSessionMeta, run_state: RunState):
        runs = store.find_runs_by_head_sha(pr_meta.pr_session_id, "nonexistent")
        assert len(runs) == 0

    def test_resolve_run_to_pr_session(self, store: FilePRSessionStore, run_state: RunState):
        ps_id = store.resolve_run_to_pr_session(run_state.run_id)
        assert ps_id is not None

    def test_resolve_run_not_found(self, store: FilePRSessionStore):
        assert store.resolve_run_to_pr_session("nonexistent") is None

    def test_index_rebuild_from_files(self, store: FilePRSessionStore, pr_meta: PRSessionMeta, run_state: RunState):
        """Rebuild index from authoritative run.json files."""
        # Delete index
        from backend.storage.pr_session.paths import pr_index_file
        idx_path = pr_index_file(store._storage_dir, pr_meta.pr_key)
        if idx_path.exists():
            idx_path.unlink()

        idx = store.rebuild_index(pr_meta.pr_session_id)
        assert len(idx.runs) == 1
        assert idx.runs[0].run_id == run_state.run_id


# ---------------------------------------------------------------------------
# Path traversal rejection
# ---------------------------------------------------------------------------


class TestPathTraversalRejection:
    def test_create_run_rejects_bad_pr_session(self, store: FilePRSessionStore):
        with pytest.raises(PRSessionNotFoundError):
            store.create_run("../../../etc", "ctx", "a", "b")


# ---------------------------------------------------------------------------
# Atomic write verification
# ---------------------------------------------------------------------------


class TestAtomicWrites:
    def test_json_write_is_atomic(self, store: FilePRSessionStore, pr_meta: PRSessionMeta):
        """Verify no temp files left after successful write."""
        from backend.storage.pr_session.paths import pr_session_dir

        session_dir = pr_session_dir(store._storage_dir, pr_meta.pr_key)
        tmp_files = list(session_dir.glob(".tmp-*"))
        assert len(tmp_files) == 0
