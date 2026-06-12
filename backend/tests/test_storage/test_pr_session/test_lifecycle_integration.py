"""Integration tests for full run lifecycle with persistence.

These tests exercise the real FilePRSessionStore alongside the RunManager,
verifying that durable state on disk matches in-memory state at each
lifecycle transition.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.agent.runtime.events import RunEvent, RunStatus
from backend.agent.runtime.run_manager import RunManager
from backend.storage.pr_session.models import (
    ContextRecord,
    RunLifecycle,
)
from backend.storage.pr_session.run_persistence import (
    create_durable_run,
    persist_context,
    persist_event,
    persist_lifecycle_transition,
    persist_result,
)
from backend.storage.pr_session.store import FilePRSessionStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path):
    """Provide a fresh FilePRSessionStore."""
    return FilePRSessionStore(tmp_path / "storage")


@pytest.fixture
def run_manager():
    """Provide a fresh RunManager."""
    return RunManager()


def _make_ctx_record(
    pr_session_id: str,
    context_id: str,
    *,
    owner: str = "test-owner",
    repo: str = "test-repo",
    pull_number: int = 42,
) -> ContextRecord:
    """Create a minimal ContextRecord for testing."""
    return ContextRecord(
        context_id=context_id,
        pr_session_id=pr_session_id,
        owner=owner,
        repo=repo,
        pull_number=pull_number,
        head_sha="abc123def456",
        base_sha="000000000000",
        pr_metadata={
            "title": "Test PR",
            "author": "tester",
            "url": "https://github.com/test-owner/test-repo/pull/42",
            "state": "open",
            "base_branch": "main",
            "head_branch": "feature",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:00Z",
        },
        commits={"head_sha": "abc123def456", "commits": []},
        files=[],
        derived=None,
    )


# ---------------------------------------------------------------------------
# Successful run lifecycle
# ---------------------------------------------------------------------------


class TestSuccessfulRunLifecycle:
    """queued -> running -> completed with result persistence."""

    def test_full_success_lifecycle(self, store, run_manager):
        # Create in-memory run
        mem_run = run_manager.create_run("ctx_success_1")
        run_id = mem_run.run_id
        assert mem_run.status == RunStatus.QUEUED

        # Create durable run
        pr_meta, run_state = create_durable_run(
            store,
            owner="acme",
            repo="widgets",
            pull_number=10,
            context_id="ctx_success_1",
            base_sha="aaa",
            head_sha="bbb",
        )
        durable_run_id = run_state.run_id
        assert run_state.lifecycle == RunLifecycle.QUEUED

        # Persist context
        ctx_record = _make_ctx_record(
            pr_meta.pr_session_id, "ctx_success_1", owner="acme", repo="widgets", pull_number=10
        )
        persist_context(store, durable_run_id, ctx_record)

        # Transition to RUNNING (in-memory + durable)
        run_manager.mark_running(run_id)
        persist_lifecycle_transition(store, durable_run_id, RunStatus.RUNNING)

        # Verify durable state
        state = store.get_run_state(durable_run_id)
        assert state.lifecycle == RunLifecycle.RUNNING

        # Emit some events
        for i in range(3):
            event = RunEvent(
                run_id=durable_run_id,
                type="agent.message",
                payload={"content": f"Step {i}"},
                event_id=f"ev_{i}",
                sequence=i,
            )
            persist_event(store, event)

        # Complete the run
        findings = [{"id": "f1", "title": "Found something"}]
        run_manager.complete_run(run_id, findings)
        persist_lifecycle_transition(store, durable_run_id, RunStatus.COMPLETED)

        persist_result(
            store,
            durable_run_id,
            pr_meta.pr_session_id,
            "completed",
            findings=[{"id": "f1", "title": "Found something", "severity": "medium"}],
            coverage={"files_reviewed": 1, "total_files": 1},
            usage={"prompt_tokens": 1000, "completion_tokens": 500},
        )

        # Verify final durable state
        final_state = store.get_run_state(durable_run_id)
        assert final_state.lifecycle == RunLifecycle.COMPLETED

        loaded_result = store.load_result(durable_run_id)
        assert loaded_result is not None
        assert len(loaded_result.findings) == 1
        assert loaded_result.findings[0]["title"] == "Found something"

        # Verify events persisted
        events = store.load_events(durable_run_id)
        assert len(events) == 3
        assert events[0].payload["content"] == "Step 0"

        # Verify context persisted
        loaded_ctx = store.load_context(durable_run_id)
        assert loaded_ctx is not None
        assert loaded_ctx.owner == "acme"

        # Verify index
        idx = store.get_index(pr_meta.pr_session_id)
        assert len(idx.runs) == 1
        assert idx.runs[0].run_id == durable_run_id
        assert idx.runs[0].lifecycle == RunLifecycle.COMPLETED


# ---------------------------------------------------------------------------
# Failed run lifecycle
# ---------------------------------------------------------------------------


class TestFailedRunLifecycle:
    """queued -> running -> failed with error summary."""

    def test_full_failure_lifecycle(self, store, run_manager):
        mem_run = run_manager.create_run("ctx_fail_1")
        run_id = mem_run.run_id

        pr_meta, run_state = create_durable_run(
            store,
            owner="acme",
            repo="widgets",
            pull_number=11,
            context_id="ctx_fail_1",
            base_sha="aaa",
            head_sha="ccc",
        )
        durable_run_id = run_state.run_id

        # Transition to RUNNING
        run_manager.mark_running(run_id)
        persist_lifecycle_transition(store, durable_run_id, RunStatus.RUNNING)

        # Emit an error event
        error_event = RunEvent(
            run_id=durable_run_id,
            type="run.error",
            payload={"error": "Rate limit exceeded"},
            event_id="ev_err",
            sequence=0,
        )
        persist_event(store, error_event)

        # Fail the run
        error_summary = "OpenAI API rate limit exceeded after 3 retries"
        run_manager.fail_run(run_id, error_summary)
        persist_lifecycle_transition(
            store, durable_run_id, RunStatus.FAILED, error_summary=error_summary
        )

        persist_result(
            store,
            durable_run_id,
            pr_meta.pr_session_id,
            "failed",
            error_summary=error_summary,
        )

        # Verify
        state = store.get_run_state(durable_run_id)
        assert state.lifecycle == RunLifecycle.FAILED

        loaded_result = store.load_result(durable_run_id)
        assert loaded_result is not None
        assert loaded_result.lifecycle == "failed"
        assert loaded_result.error_summary == error_summary

        events = store.load_events(durable_run_id)
        assert len(events) == 1
        assert events[0].event_type == "run.error"

        # In-memory status
        status = run_manager.get_status(run_id)
        assert status["status"] == "failed"


# ---------------------------------------------------------------------------
# Cancelled run lifecycle
# ---------------------------------------------------------------------------


class TestCancelledRunLifecycle:
    """queued -> running -> cancelling -> cancelled."""

    def test_full_cancel_lifecycle(self, store, run_manager):
        mem_run = run_manager.create_run("ctx_cancel_1")
        run_id = mem_run.run_id

        pr_meta, run_state = create_durable_run(
            store,
            owner="acme",
            repo="widgets",
            pull_number=12,
            context_id="ctx_cancel_1",
            base_sha="aaa",
            head_sha="ddd",
        )
        durable_run_id = run_state.run_id

        # Transition to RUNNING
        run_manager.mark_running(run_id)
        persist_lifecycle_transition(store, durable_run_id, RunStatus.RUNNING)

        # Request cancellation
        run_manager.cancel_run(run_id)
        persist_lifecycle_transition(store, durable_run_id, RunStatus.CANCELLING)

        state = store.get_run_state(durable_run_id)
        assert state.lifecycle == RunLifecycle.CANCELLING

        # Observe cancellation (agent acknowledges)
        run_manager.observe_cancellation(run_id)
        persist_lifecycle_transition(store, durable_run_id, RunStatus.CANCELLED)

        final_state = store.get_run_state(durable_run_id)
        assert final_state.lifecycle == RunLifecycle.CANCELLED
        assert final_state.completed_at is not None


# ---------------------------------------------------------------------------
# Workspace-preparation-failed run
# ---------------------------------------------------------------------------


class TestWorkspacePreparationFailedRun:
    """queued -> failed due to workspace preparation error."""

    def test_workspace_prep_failure(self, store, run_manager):
        mem_run = run_manager.create_run("ctx_ws_fail_1")
        run_id = mem_run.run_id

        pr_meta, run_state = create_durable_run(
            store,
            owner="acme",
            repo="widgets",
            pull_number=13,
            context_id="ctx_ws_fail_1",
            base_sha="aaa",
            head_sha="eee",
        )
        durable_run_id = run_state.run_id

        # Fail immediately (workspace prep failed before running)
        error_summary = "Failed to clone repository: authentication failed"
        run_manager.fail_run(run_id, error_summary)
        persist_lifecycle_transition(
            store, durable_run_id, RunStatus.FAILED, error_summary=error_summary
        )
        persist_result(
            store,
            durable_run_id,
            pr_meta.pr_session_id,
            "failed",
            error_summary=error_summary,
        )

        # Verify - should go directly from QUEUED to FAILED
        state = store.get_run_state(durable_run_id)
        assert state.lifecycle == RunLifecycle.FAILED

        loaded_result = store.load_result(durable_run_id)
        assert loaded_result is not None
        assert "authentication failed" in loaded_result.error_summary

        # Index should reflect the failure
        idx = store.get_index(pr_meta.pr_session_id)
        assert idx.runs[0].lifecycle == RunLifecycle.FAILED


# ---------------------------------------------------------------------------
# Recovery after restart
# ---------------------------------------------------------------------------


class TestRecoveryIntegration:
    """Verify that terminal runs survive a simulated restart."""

    def test_completed_run_survives_restart(self, store, run_manager):
        # Phase 1: Create and complete a run
        mem_run = run_manager.create_run("ctx_recovery_1")
        run_id = mem_run.run_id

        pr_meta, run_state = create_durable_run(
            store,
            owner="acme",
            repo="widgets",
            pull_number=14,
            context_id="ctx_recovery_1",
            base_sha="aaa",
            head_sha="fff",
        )
        durable_run_id = run_state.run_id

        # Persist context for hydration test
        ctx_record = _make_ctx_record(
            pr_meta.pr_session_id, "ctx_recovery_1", owner="acme", repo="widgets", pull_number=14
        )
        persist_context(store, durable_run_id, ctx_record)

        run_manager.mark_running(run_id)
        persist_lifecycle_transition(store, durable_run_id, RunStatus.RUNNING)

        # Emit events
        for i in range(5):
            event = RunEvent(
                run_id=durable_run_id,
                type="agent.message",
                payload={"content": f"Message {i}"},
                event_id=f"ev_{i}",
                sequence=i,
            )
            persist_event(store, event)

        run_manager.complete_run(run_id, [{"id": "f1"}])
        persist_lifecycle_transition(store, durable_run_id, RunStatus.COMPLETED)
        persist_result(
            store,
            durable_run_id,
            pr_meta.pr_session_id,
            "completed",
            findings=[{"id": "f1"}],
        )

        # Phase 2: Simulate restart - new RunManager
        new_run_manager = RunManager()

        from backend.storage.pr_session.recovery import recover_on_startup

        report = recover_on_startup(store, new_run_manager)

        assert report.terminal_runs_restored == 1
        assert report.interrupted_runs == 0
        assert report.corrupt_runs_skipped == 0

        # Verify the run is accessible in the new RunManager
        status = new_run_manager.get_status(durable_run_id)
        assert status["status"] == "completed"
        assert status["final_result"]["findings"] == [{"id": "f1"}]

        # Verify context was hydrated
        from backend.domain.pr_context.context_manager import get_context

        ctx = get_context("ctx_recovery_1")
        assert ctx is not None
        assert ctx.owner == "acme"
        assert ctx.source == "persisted"

    def test_interrupted_run_survives_restart(self, store, run_manager):
        # Phase 1: Create a run that's still running when restart happens
        mem_run = run_manager.create_run("ctx_interrupt_1")
        run_id = mem_run.run_id

        pr_meta, run_state = create_durable_run(
            store,
            owner="acme",
            repo="widgets",
            pull_number=15,
            context_id="ctx_interrupt_1",
            base_sha="aaa",
            head_sha="ggg",
        )
        durable_run_id = run_state.run_id

        run_manager.mark_running(run_id)
        persist_lifecycle_transition(store, durable_run_id, RunStatus.RUNNING)

        # Emit partial events
        for i in range(2):
            event = RunEvent(
                run_id=durable_run_id,
                type="agent.message",
                payload={"content": f"Partial {i}"},
                event_id=f"ev_{i}",
                sequence=i,
            )
            persist_event(store, event)

        # Simulate crash (no graceful shutdown) - just start recovery
        new_run_manager = RunManager()

        from backend.storage.pr_session.recovery import recover_on_startup

        report = recover_on_startup(store, new_run_manager)

        assert report.interrupted_runs == 1
        assert report.terminal_runs_restored == 0

        # Verify the run is now INTERRUPTED on disk
        state = store.get_run_state(durable_run_id)
        assert state.lifecycle == RunLifecycle.INTERRUPTED

    def test_event_replay_after_recovery(self, store, run_manager):
        """Events persisted before crash are replayable after recovery."""
        mem_run = run_manager.create_run("ctx_replay_1")
        run_id = mem_run.run_id

        pr_meta, run_state = create_durable_run(
            store,
            owner="acme",
            repo="widgets",
            pull_number=16,
            context_id="ctx_replay_1",
            base_sha="aaa",
            head_sha="hhh",
        )
        durable_run_id = run_state.run_id

        run_manager.mark_running(run_id)
        persist_lifecycle_transition(store, durable_run_id, RunStatus.RUNNING)

        # Emit events with specific types
        event_types = ["agent.message", "tool.call", "agent.message", "subagent.start", "run.complete"]
        for i, etype in enumerate(event_types):
            event = RunEvent(
                run_id=durable_run_id,
                type=etype,
                payload={"index": i},
                event_id=f"ev_{i}",
                sequence=i,
            )
            persist_event(store, event)

        run_manager.complete_run(run_id, [])
        persist_lifecycle_transition(store, durable_run_id, RunStatus.COMPLETED)
        persist_result(
            store,
            durable_run_id,
            pr_meta.pr_session_id,
            "completed",
        )

        # Recover
        new_run_manager = RunManager()
        from backend.storage.pr_session.recovery import recover_on_startup

        recover_on_startup(store, new_run_manager)

        # Replay events from persisted store (simulating WebSocket reconnect)
        events = store.load_events(durable_run_id, after_sequence=-1, limit=200)
        assert len(events) == 5
        assert [e.event_type for e in events] == event_types

        # Replay from a specific sequence (after_sequence=2)
        partial = store.load_events(durable_run_id, after_sequence=2, limit=200)
        assert len(partial) == 2
        assert partial[0].sequence == 3
        assert partial[1].sequence == 4


# ---------------------------------------------------------------------------
# Multiple runs for same PR
# ---------------------------------------------------------------------------


class TestMultipleRunsForSamePR:
    """Verify multiple runs for the same PR are correctly tracked."""

    def test_two_runs_different_head_shas(self, store):
        # First run
        pr_meta, run_state_1 = create_durable_run(
            store,
            owner="acme",
            repo="widgets",
            pull_number=20,
            context_id="ctx_multi_1",
            base_sha="aaa",
            head_sha="v1",
        )
        run_id_1 = run_state_1.run_id

        # Complete first run
        store.update_run_state(
            run_id_1, lifecycle=RunLifecycle.COMPLETED, completed_at="2025-01-01T00:00:00Z"
        )
        persist_result(
            store,
            run_id_1,
            pr_meta.pr_session_id,
            "completed",
            findings=[{"id": "f1", "title": "Finding in v1"}],
        )

        # Second run (same PR, different head SHA)
        _, run_state_2 = create_durable_run(
            store,
            owner="acme",
            repo="widgets",
            pull_number=20,
            context_id="ctx_multi_2",
            base_sha="aaa",
            head_sha="v2",
        )
        run_id_2 = run_state_2.run_id

        # Complete second run
        store.update_run_state(
            run_id_2, lifecycle=RunLifecycle.COMPLETED, completed_at="2025-01-02T00:00:00Z"
        )
        persist_result(
            store,
            run_id_2,
            pr_meta.pr_session_id,
            "completed",
            findings=[{"id": "f2", "title": "Finding in v2"}],
        )

        # Verify index has both runs
        idx = store.get_index(pr_meta.pr_session_id)
        assert len(idx.runs) == 2

        # Verify find by head SHA
        v1_runs = store.find_runs_by_head_sha(pr_meta.pr_session_id, "v1")
        assert len(v1_runs) == 1
        assert v1_runs[0].run_id == run_id_1

        v2_runs = store.find_runs_by_head_sha(pr_meta.pr_session_id, "v2")
        assert len(v2_runs) == 1
        assert v2_runs[0].run_id == run_id_2

        # Verify each result
        loaded_1 = store.load_result(run_id_1)
        assert loaded_1.findings[0]["title"] == "Finding in v1"

        loaded_2 = store.load_result(run_id_2)
        assert loaded_2.findings[0]["title"] == "Finding in v2"

    def test_retry_creates_new_run(self, store):
        """A retry creates a new run referencing the original."""
        from backend.storage.pr_session.recovery import create_retry_run

        pr_meta, original = create_durable_run(
            store,
            owner="acme",
            repo="widgets",
            pull_number=21,
            context_id="ctx_retry_1",
            base_sha="aaa",
            head_sha="iii",
        )

        # Mark as interrupted
        store.update_run_state(
            original.run_id,
            lifecycle=RunLifecycle.INTERRUPTED,
            completed_at="2025-01-01T00:00:00Z",
        )

        # Create retry
        retry_state = create_retry_run(store, original.run_id, "ctx_retry_2")
        assert retry_state is not None
        assert retry_state.retry_of_run_id == original.run_id
        assert retry_state.lifecycle == RunLifecycle.QUEUED

        # Original still exists
        orig = store.get_run_state(original.run_id)
        assert orig.lifecycle == RunLifecycle.INTERRUPTED

        # Index has both
        idx = store.get_index(pr_meta.pr_session_id)
        assert len(idx.runs) == 2
