"""Tests for retention policy and integrity scanning."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.storage.pr_session.models import (
    PRSessionMeta,
    RetentionPolicy,
    RunLifecycle,
)
from backend.storage.pr_session.retention import (
    CleanupPlan,
    load_retention_policy_from_env,
    plan_cleanup,
    scan_integrity,
)
from backend.storage.pr_session.store import FilePRSessionStore


@pytest.fixture
def store(tmp_path: Path) -> FilePRSessionStore:
    return FilePRSessionStore(tmp_path / "storage")


@pytest.fixture
def pr_with_runs(store: FilePRSessionStore):
    meta = store.get_or_create_pr_session("octocat", "hello", 42)
    r1 = store.create_run(meta.pr_session_id, "ctx-1", "aaa", "bbb")
    r2 = store.create_run(meta.pr_session_id, "ctx-2", "aaa", "ccc")
    store.update_run_state(r1.run_id, lifecycle=RunLifecycle.COMPLETED)
    store.update_run_state(r2.run_id, lifecycle=RunLifecycle.FAILED, error_summary="err")
    return meta, r1, r2


# ---------------------------------------------------------------------------
# Retention policy from environment
# ---------------------------------------------------------------------------


class TestRetentionPolicyFromEnv:
    def test_defaults(self, monkeypatch):
        policy = load_retention_policy_from_env()
        assert policy.result_days == 90
        assert policy.event_days == 30

    def test_override_from_env(self, monkeypatch):
        monkeypatch.setenv("PR_COPILOT_RETENTION_RESULT_DAYS", "60")
        monkeypatch.setenv("PR_COPILOT_RETENTION_EVENT_DAYS", "7")
        policy = load_retention_policy_from_env()
        assert policy.result_days == 60
        assert policy.event_days == 7

    def test_invalid_env_uses_default(self, monkeypatch):
        monkeypatch.setenv("PR_COPILOT_RETENTION_RESULT_DAYS", "not_a_number")
        policy = load_retention_policy_from_env()
        assert policy.result_days == 90


# ---------------------------------------------------------------------------
# Cleanup planning
# ---------------------------------------------------------------------------


class TestCleanupPlanning:
    def test_empty_store(self, store: FilePRSessionStore):
        policy = RetentionPolicy()
        plan = plan_cleanup(store, policy)
        assert len(plan.files) == 0

    def test_skips_active_runs(self, store: FilePRSessionStore):
        meta = store.get_or_create_pr_session("octocat", "hello", 42)
        rs = store.create_run(meta.pr_session_id, "ctx-1", "aaa", "bbb")
        # Run is QUEUED by default - should be skipped

        policy = RetentionPolicy(event_days=0, result_days=0)
        plan = plan_cleanup(store, policy)
        assert len(plan.skipped_active) == 1

    def test_reports_expired_files(self, store: FilePRSessionStore):
        meta = store.get_or_create_pr_session("octocat", "hello", 42)
        rs = store.create_run(meta.pr_session_id, "ctx-1", "aaa", "bbb")
        store.update_run_state(rs.run_id, lifecycle=RunLifecycle.COMPLETED)

        # Write events
        from backend.storage.pr_session.models import PersistedEvent
        store.append_event(PersistedEvent(
            event_id="e0", run_id=rs.run_id,
            sequence=0, event_type="run.started", created_at="t",
        ))

        # Policy with -1 days = everything is expired (age 0 > -1)
        policy = RetentionPolicy(event_days=-1, result_days=-1, context_days=-1)
        plan = plan_cleanup(store, policy)
        # Should find at least the events file
        event_files = [f for f in plan.files if f["type"] == "events"]
        assert len(event_files) >= 1


# ---------------------------------------------------------------------------
# Integrity scanning
# ---------------------------------------------------------------------------


class TestIntegrityScanning:
    def test_clean_session(self, store: FilePRSessionStore, pr_with_runs):
        meta, r1, r2 = pr_with_runs
        report = scan_integrity(store, meta.pr_session_id)
        # Should be clean or have only minor warnings
        assert report.error_count == 0

    def test_missing_run_json(self, store: FilePRSessionStore):
        meta = store.get_or_create_pr_session("octocat", "hello", 42)
        rs = store.create_run(meta.pr_session_id, "ctx-1", "aaa", "bbb")

        # Delete run.json
        from backend.storage.pr_session.paths import run_state_file
        rsf = run_state_file(store._storage_dir, meta.pr_key, rs.run_id)
        rsf.unlink()

        report = scan_integrity(store, meta.pr_session_id)
        assert report.error_count >= 1

    def test_corrupt_run_json(self, store: FilePRSessionStore):
        meta = store.get_or_create_pr_session("octocat", "hello", 42)
        rs = store.create_run(meta.pr_session_id, "ctx-1", "aaa", "bbb")

        # Corrupt run.json
        from backend.storage.pr_session.paths import run_state_file
        rsf = run_state_file(store._storage_dir, meta.pr_key, rs.run_id)
        rsf.write_text("NOT JSON", encoding="utf-8")

        report = scan_integrity(store, meta.pr_session_id)
        assert report.error_count >= 1

    def test_corrupt_events_jsonl(self, store: FilePRSessionStore, pr_with_runs):
        meta, r1, r2 = pr_with_runs

        # Write corrupt events
        from backend.storage.pr_session.paths import run_events_file
        ef = run_events_file(store._storage_dir, meta.pr_key, r1.run_id)
        ef.parent.mkdir(parents=True, exist_ok=True)
        with open(ef, "w", encoding="utf-8") as f:
            f.write("NOT VALID JSON\n")

        report = scan_integrity(store, meta.pr_session_id)
        warnings = [f for f in report.findings if f.level.value == "warning"]
        assert len(warnings) >= 1

    def test_non_terminal_run_detected(self, store: FilePRSessionStore):
        meta = store.get_or_create_pr_session("octocat", "hello", 42)
        rs = store.create_run(meta.pr_session_id, "ctx-1", "aaa", "bbb")
        # Run stays QUEUED - should be a warning

        report = scan_integrity(store, meta.pr_session_id)
        warnings = [f for f in report.findings if "Non-terminal" in f.message]
        assert len(warnings) >= 1

    def test_missing_index_warns(self, store: FilePRSessionStore):
        meta = store.get_or_create_pr_session("octocat", "hello", 42)
        store.create_run(meta.pr_session_id, "ctx-1", "aaa", "bbb")

        # Ensure index exists first, then delete it
        store.get_index(meta.pr_session_id)
        from backend.storage.pr_session.paths import pr_index_file
        idx = pr_index_file(store._storage_dir, meta.pr_key)
        assert idx.exists()
        idx.unlink()

        report = scan_integrity(store, meta.pr_session_id)
        idx_warnings = [f for f in report.findings if f.component == "index.json"]
        assert len(idx_warnings) >= 1

    def test_nonexistent_session(self, store: FilePRSessionStore):
        report = scan_integrity(store, "nonexistent")
        assert report.error_count >= 1
