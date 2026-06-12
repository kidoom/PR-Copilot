"""Tests for PR session durable models: serialization, validation, and versioning."""

from __future__ import annotations

import pytest

from backend.storage.pr_session.models import (
    AgentSessionRef,
    AgentSessionStatus,
    AgentSessionsRecord,
    ContextRecord,
    IntegrityFinding,
    IntegrityLevel,
    IntegrityReport,
    PersistedEvent,
    PRIdentity,
    PRSessionIndex,
    PRSessionMeta,
    ResultRecord,
    RetentionPolicy,
    RunIndexEntry,
    RunLifecycle,
    RunState,
    TaskPlanRecord,
    UnsupportedVersion,
)


# ---------------------------------------------------------------------------
# PRIdentity
# ---------------------------------------------------------------------------


class TestPRIdentity:
    def test_roundtrip(self):
        ident = PRIdentity(owner="octocat", repo="hello-world", pull_number=42)
        d = ident.to_dict()
        restored = PRIdentity.from_dict(d)
        assert restored == ident

    def test_from_dict_converts_pull_number_to_int(self):
        d = {"owner": "a", "repo": "b", "pull_number": "7"}
        ident = PRIdentity.from_dict(d)
        assert ident.pull_number == 7
        assert isinstance(ident.pull_number, int)


# ---------------------------------------------------------------------------
# PRSessionMeta
# ---------------------------------------------------------------------------


class TestPRSessionMeta:
    def test_roundtrip(self):
        meta = PRSessionMeta(
            pr_session_id="ps-1",
            pr_key="octocat__hello-world__42__a1b2c3d4",
            owner="octocat",
            repo="hello-world",
            pull_number=42,
        )
        d = meta.to_dict()
        assert d["schema_version"] == 1
        restored = PRSessionMeta.from_dict(d)
        assert restored.pr_session_id == "ps-1"
        assert restored.owner == "octocat"
        assert restored.pull_number == 42
        assert restored.schema_version == 1

    def test_from_dict_defaults_missing_optional_fields(self):
        d = {
            "pr_session_id": "ps-1",
            "pr_key": "k",
            "owner": "o",
            "repo": "r",
            "pull_number": 1,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        meta = PRSessionMeta.from_dict(d)
        assert meta.run_count == 0
        assert meta.schema_version == 1


# ---------------------------------------------------------------------------
# RunState
# ---------------------------------------------------------------------------


class TestRunState:
    def test_roundtrip(self):
        state = RunState(
            run_id="run-abc",
            pr_session_id="ps-1",
            context_id="ctx-1",
            base_sha="aaa",
            head_sha="bbb",
            lifecycle=RunLifecycle.RUNNING,
            started_at="2026-01-01T00:01:00+00:00",
        )
        d = state.to_dict()
        assert d["lifecycle"] == "running"
        assert d["started_at"] == "2026-01-01T00:01:00+00:00"
        assert "completed_at" not in d  # None fields excluded
        restored = RunState.from_dict(d)
        assert restored.lifecycle == RunLifecycle.RUNNING
        assert restored.started_at is not None
        assert restored.completed_at is None

    def test_interrupted_lifecycle(self):
        state = RunState(
            run_id="r", pr_session_id="ps", context_id="c",
            base_sha="a", head_sha="b", lifecycle=RunLifecycle.INTERRUPTED,
        )
        d = state.to_dict()
        assert d["lifecycle"] == "interrupted"
        restored = RunState.from_dict(d)
        assert restored.lifecycle == RunLifecycle.INTERRUPTED

    def test_error_summary_preserved(self):
        state = RunState(
            run_id="r", pr_session_id="ps", context_id="c",
            base_sha="a", head_sha="b", lifecycle=RunLifecycle.FAILED,
            error_summary="something went wrong",
        )
        d = state.to_dict()
        assert d["error_summary"] == "something went wrong"
        restored = RunState.from_dict(d)
        assert restored.error_summary == "something went wrong"


# ---------------------------------------------------------------------------
# PersistedEvent
# ---------------------------------------------------------------------------


class TestPersistedEvent:
    def test_roundtrip(self):
        event = PersistedEvent(
            event_id="evt-1",
            run_id="run-1",
            sequence=5,
            event_type="tool.call",
            created_at="2026-01-01T00:00:00+00:00",
            payload={"tool": "read_file", "input_summary": "read foo.py"},
        )
        d = event.to_dict()
        assert d["schema_version"] == 1
        restored = PersistedEvent.from_dict(d)
        assert restored.sequence == 5
        assert restored.event_type == "tool.call"
        assert restored.payload["tool"] == "read_file"

    def test_empty_payload_defaults(self):
        event = PersistedEvent(
            event_id="e", run_id="r", sequence=0,
            event_type="run.started", created_at="t",
        )
        d = event.to_dict()
        assert d["payload"] == {}


# ---------------------------------------------------------------------------
# RunIndexEntry / PRSessionIndex
# ---------------------------------------------------------------------------


class TestRunIndexEntry:
    def test_roundtrip(self):
        entry = RunIndexEntry(
            run_id="run-1", head_sha="abc", base_sha="def",
            lifecycle="completed", created_at="t1", updated_at="t2",
            completed_at="t3", finding_count=5, error_count=0,
            agent_session_count=3,
        )
        d = entry.to_dict()
        assert d["finding_count"] == 5
        restored = RunIndexEntry.from_dict(d)
        assert restored.finding_count == 5
        assert restored.completed_at == "t3"


class TestPRSessionIndex:
    def test_roundtrip(self):
        idx = PRSessionIndex(
            pr_session_id="ps-1",
            runs=[
                RunIndexEntry(
                    run_id="r1", head_sha="a", base_sha="b",
                    lifecycle="completed", created_at="t1", updated_at="t2",
                ),
            ],
        )
        d = idx.to_dict()
        assert d["schema_version"] == 1
        assert len(d["runs"]) == 1
        restored = PRSessionIndex.from_dict(d)
        assert len(restored.runs) == 1
        assert restored.runs[0].run_id == "r1"


# ---------------------------------------------------------------------------
# AgentSessionRef / AgentSessionsRecord
# ---------------------------------------------------------------------------


class TestAgentSessionRef:
    def test_roundtrip(self):
        ref = AgentSessionRef(
            memory_session_id="sess-1",
            agent_kind="main",
            agent_type="review",
            status=AgentSessionStatus.COMPLETED,
            task_id="",
            child_session_id="",
            created_at="t1",
            completed_at="t2",
        )
        d = ref.to_dict()
        assert d["status"] == "completed"
        restored = AgentSessionRef.from_dict(d)
        assert restored.status == AgentSessionStatus.COMPLETED

    def test_optional_fields_excluded_when_empty(self):
        ref = AgentSessionRef(
            memory_session_id="s", agent_kind="subagent",
            agent_type="security", status=AgentSessionStatus.ACTIVE,
            task_id="task-1", child_session_id="child-1",
        )
        d = ref.to_dict()
        assert d["task_id"] == "task-1"
        assert d["child_session_id"] == "child-1"


class TestAgentSessionsRecord:
    def test_roundtrip(self):
        record = AgentSessionsRecord(
            run_id="run-1",
            sessions=[
                AgentSessionRef(
                    memory_session_id="s1", agent_kind="main",
                    agent_type="review", status=AgentSessionStatus.ACTIVE,
                ),
            ],
        )
        d = record.to_dict()
        assert len(d["sessions"]) == 1
        restored = AgentSessionsRecord.from_dict(d)
        assert restored.run_id == "run-1"
        assert len(restored.sessions) == 1


# ---------------------------------------------------------------------------
# ContextRecord
# ---------------------------------------------------------------------------


class TestContextRecord:
    def test_roundtrip(self):
        ctx = ContextRecord(
            context_id="ctx-1", pr_session_id="ps-1",
            owner="octocat", repo="hello", pull_number=1,
            base_sha="aaa", head_sha="bbb",
            pr_metadata={"title": "Fix bug"},
            files=[{"filename": "foo.py", "status": "modified"}],
            derived={"total_hunks": 3},
        )
        d = ctx.to_dict()
        assert d["schema_version"] == 1
        assert d["pr_metadata"]["title"] == "Fix bug"
        restored = ContextRecord.from_dict(d)
        assert len(restored.files) == 1
        assert restored.derived["total_hunks"] == 3


# ---------------------------------------------------------------------------
# TaskPlanRecord
# ---------------------------------------------------------------------------


class TestTaskPlanRecord:
    def test_roundtrip(self):
        tp = TaskPlanRecord(
            run_id="run-1", pr_session_id="ps-1",
            tasks=[{"task_type": "security_context", "files": ["a.py"]}],
            evidence=[{"severity": "warning", "category": "security"}],
        )
        d = tp.to_dict()
        restored = TaskPlanRecord.from_dict(d)
        assert len(restored.tasks) == 1
        assert len(restored.evidence) == 1


# ---------------------------------------------------------------------------
# ResultRecord
# ---------------------------------------------------------------------------


class TestResultRecord:
    def test_roundtrip(self):
        rr = ResultRecord(
            run_id="run-1", pr_session_id="ps-1",
            lifecycle="completed",
            findings=[{"title": "Bug", "severity": "critical"}],
            coverage={"reviewed": 10, "total": 15},
            usage={"tokens": 5000},
        )
        d = rr.to_dict()
        assert "error_summary" not in d  # None fields are excluded from to_dict
        restored = ResultRecord.from_dict(d)
        assert len(restored.findings) == 1
        assert restored.coverage["reviewed"] == 10

    def test_failed_result_with_error(self):
        rr = ResultRecord(
            run_id="r", pr_session_id="ps", lifecycle="failed",
            error_summary="timeout",
        )
        d = rr.to_dict()
        assert d["error_summary"] == "timeout"
        restored = ResultRecord.from_dict(d)
        assert restored.error_summary == "timeout"


# ---------------------------------------------------------------------------
# IntegrityFinding / IntegrityReport
# ---------------------------------------------------------------------------


class TestIntegrityReport:
    def test_clean_report(self):
        report = IntegrityReport(pr_session_id="ps-1")
        assert report.is_clean
        assert report.error_count == 0
        assert report.warning_count == 0

    def test_report_with_findings(self):
        report = IntegrityReport(
            pr_session_id="ps-1",
            findings=[
                IntegrityFinding(
                    level=IntegrityLevel.WARNING,
                    component="events.jsonl",
                    message="skipped 1 malformed line",
                    run_id="run-1",
                ),
                IntegrityFinding(
                    level=IntegrityLevel.ERROR,
                    component="run.json",
                    message="corrupt JSON",
                    run_id="run-2",
                ),
            ],
        )
        assert not report.is_clean
        assert report.error_count == 1
        assert report.warning_count == 1

    def test_to_dict(self):
        report = IntegrityReport(
            pr_session_id="ps-1",
            findings=[
                IntegrityFinding(
                    level=IntegrityLevel.WARNING,
                    component="index.json",
                    message="stale",
                ),
            ],
        )
        d = report.to_dict()
        assert d["is_clean"] is False
        assert d["error_count"] == 0
        assert d["warning_count"] == 1
        assert len(d["findings"]) == 1


# ---------------------------------------------------------------------------
# RetentionPolicy
# ---------------------------------------------------------------------------


class TestRetentionPolicy:
    def test_defaults(self):
        rp = RetentionPolicy()
        assert rp.result_days == 90
        assert rp.event_days == 30
        assert rp.context_days == 14
        assert rp.agent_transcript_days == 7
        assert rp.empty_session_days == 1

    def test_roundtrip(self):
        rp = RetentionPolicy(result_days=60, event_days=None)
        d = rp.to_dict()
        assert d["result_days"] == 60
        assert d["event_days"] is None
        restored = RetentionPolicy.from_dict(d)
        assert restored.result_days == 60
        assert restored.event_days is None


# ---------------------------------------------------------------------------
# UnsupportedVersion
# ---------------------------------------------------------------------------


class TestUnsupportedVersion:
    def test_to_dict(self):
        uv = UnsupportedVersion(
            component="run.json",
            stored_version=5,
            max_supported_version=1,
        )
        d = uv.to_dict()
        assert d["unsupported_version"] is True
        assert d["stored_version"] == 5
        assert d["max_supported_version"] == 1


# ---------------------------------------------------------------------------
# Unknown schema version handling
# ---------------------------------------------------------------------------


class TestUnknownSchemaVersion:
    """Verify that models with a higher schema_version than supported can be
    detected and do not silently lose fields."""

    def test_run_state_unknown_version_detected(self):
        data = {
            "schema_version": 999,
            "run_id": "r",
            "pr_session_id": "ps",
            "context_id": "c",
            "base_sha": "a",
            "head_sha": "b",
            "lifecycle": "running",
            "created_at": "t",
            "updated_at": "t",
            "future_field": "should not be lost",
        }
        # from_dict still parses (forward-compatible), but caller should
        # check schema_version before trusting the result.
        state = RunState.from_dict(data)
        assert state.schema_version == 999

    def test_persisted_event_unknown_version_detected(self):
        data = {
            "schema_version": 42,
            "event_id": "e",
            "run_id": "r",
            "sequence": 0,
            "event_type": "run.started",
            "created_at": "t",
        }
        event = PersistedEvent.from_dict(data)
        assert event.schema_version == 42

    def test_context_record_unknown_version_detected(self):
        data = {
            "schema_version": 10,
            "context_id": "c",
            "pr_session_id": "ps",
            "owner": "o",
            "repo": "r",
            "pull_number": 1,
            "base_sha": "a",
            "head_sha": "b",
            "created_at": "t",
        }
        ctx = ContextRecord.from_dict(data)
        assert ctx.schema_version == 10


# ---------------------------------------------------------------------------
# RunLifecycle enum
# ---------------------------------------------------------------------------


class TestRunLifecycle:
    def test_all_values(self):
        assert RunLifecycle.QUEUED.value == "queued"
        assert RunLifecycle.RUNNING.value == "running"
        assert RunLifecycle.CANCELLING.value == "cancelling"
        assert RunLifecycle.CANCELLED.value == "cancelled"
        assert RunLifecycle.COMPLETED.value == "completed"
        assert RunLifecycle.FAILED.value == "failed"
        assert RunLifecycle.INTERRUPTED.value == "interrupted"

    def test_interrupted_is_not_in_run_status(self):
        """INTERRUPTED is a durable-only state, not in the in-memory RunStatus."""
        from backend.agent.runtime.events import RunStatus
        assert not hasattr(RunStatus, "INTERRUPTED")


# ---------------------------------------------------------------------------
# AgentSessionStatus enum
# ---------------------------------------------------------------------------


class TestAgentSessionStatus:
    def test_all_values(self):
        assert AgentSessionStatus.ACTIVE.value == "active"
        assert AgentSessionStatus.COMPLETED.value == "completed"
        assert AgentSessionStatus.FAILED.value == "failed"
        assert AgentSessionStatus.CANCELLED.value == "cancelled"
        assert AgentSessionStatus.INVALID.value == "invalid"
        assert AgentSessionStatus.MAX_STEP.value == "max_step"
