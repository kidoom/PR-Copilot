"""Tests for run indexes and structured queries.

Covers: repeated reviews of one SHA, new commits, stale indexes,
deleted runs, and finding lookup.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.storage.pr_session.models import (
    PRSessionMeta,
    ResultRecord,
    RunLifecycle,
    RunState,
)
from backend.storage.pr_session.store import FilePRSessionStore


@pytest.fixture
def store(tmp_path: Path) -> FilePRSessionStore:
    return FilePRSessionStore(tmp_path / "storage")


@pytest.fixture
def pr_meta(store: FilePRSessionStore) -> PRSessionMeta:
    return store.get_or_create_pr_session("octocat", "hello-world", 42)


# ---------------------------------------------------------------------------
# Repeated reviews of one SHA
# ---------------------------------------------------------------------------


class TestRepeatedReviews:
    def test_two_runs_same_head_sha(self, store: FilePRSessionStore, pr_meta: PRSessionMeta):
        r1 = store.create_run(pr_meta.pr_session_id, "ctx-1", "aaa", "bbb")
        r2 = store.create_run(pr_meta.pr_session_id, "ctx-2", "aaa", "bbb")

        runs = store.find_runs_by_head_sha(pr_meta.pr_session_id, "bbb")
        assert len(runs) == 2
        run_ids = {r.run_id for r in runs}
        assert r1.run_id in run_ids
        assert r2.run_id in run_ids

    def test_runs_independent(self, store: FilePRSessionStore, pr_meta: PRSessionMeta):
        r1 = store.create_run(pr_meta.pr_session_id, "ctx-1", "aaa", "bbb")
        r2 = store.create_run(pr_meta.pr_session_id, "ctx-2", "aaa", "bbb")

        store.update_run_state(r1.run_id, lifecycle=RunLifecycle.COMPLETED)
        store.update_run_state(r2.run_id, lifecycle=RunLifecycle.FAILED, error_summary="err")

        s1 = store.get_run_state(r1.run_id)
        s2 = store.get_run_state(r2.run_id)
        assert s1.lifecycle == RunLifecycle.COMPLETED
        assert s2.lifecycle == RunLifecycle.FAILED


# ---------------------------------------------------------------------------
# New commits
# ---------------------------------------------------------------------------


class TestNewCommits:
    def test_different_head_shas(self, store: FilePRSessionStore, pr_meta: PRSessionMeta):
        r1 = store.create_run(pr_meta.pr_session_id, "ctx-1", "aaa", "bbb")
        r2 = store.create_run(pr_meta.pr_session_id, "ctx-2", "aaa", "ccc")

        runs_bbb = store.find_runs_by_head_sha(pr_meta.pr_session_id, "bbb")
        runs_ccc = store.find_runs_by_head_sha(pr_meta.pr_session_id, "ccc")
        assert len(runs_bbb) == 1
        assert len(runs_ccc) == 1
        assert runs_bbb[0].run_id == r1.run_id
        assert runs_ccc[0].run_id == r2.run_id

    def test_list_runs_newest_first(self, store: FilePRSessionStore, pr_meta: PRSessionMeta):
        r1 = store.create_run(pr_meta.pr_session_id, "ctx-1", "aaa", "bbb")
        r2 = store.create_run(pr_meta.pr_session_id, "ctx-2", "aaa", "ccc")
        r3 = store.create_run(pr_meta.pr_session_id, "ctx-3", "aaa", "ddd")

        runs = store.list_runs(pr_meta.pr_session_id)
        assert len(runs) == 3
        # All runs should be present
        run_ids = {r.run_id for r in runs}
        assert run_ids == {r1.run_id, r2.run_id, r3.run_id}

    def test_list_runs_with_pagination(self, store: FilePRSessionStore, pr_meta: PRSessionMeta):
        for i in range(5):
            store.create_run(pr_meta.pr_session_id, f"ctx-{i}", "aaa", f"head-{i}")

        page1 = store.list_runs(pr_meta.pr_session_id, limit=2)
        assert len(page1) == 2
        page2 = store.list_runs(pr_meta.pr_session_id, limit=2, offset=2)
        assert len(page2) == 2
        page3 = store.list_runs(pr_meta.pr_session_id, limit=2, offset=4)
        assert len(page3) == 1


# ---------------------------------------------------------------------------
# Stale indexes
# ---------------------------------------------------------------------------


class TestStaleIndexes:
    def test_rebuild_after_corrupt_index(self, store: FilePRSessionStore, pr_meta: PRSessionMeta):
        r1 = store.create_run(pr_meta.pr_session_id, "ctx-1", "aaa", "bbb")

        # Corrupt the index
        from backend.storage.pr_session.paths import pr_index_file
        idx_path = pr_index_file(store._storage_dir, pr_meta.pr_key)
        idx_path.write_text("NOT VALID JSON", encoding="utf-8")

        # Should rebuild automatically
        idx = store.get_index(pr_meta.pr_session_id)
        assert len(idx.runs) == 1
        assert idx.runs[0].run_id == r1.run_id

    def test_rebuild_after_missing_index(self, store: FilePRSessionStore, pr_meta: PRSessionMeta):
        r1 = store.create_run(pr_meta.pr_session_id, "ctx-1", "aaa", "bbb")

        # Delete the index
        from backend.storage.pr_session.paths import pr_index_file
        idx_path = pr_index_file(store._storage_dir, pr_meta.pr_key)
        if idx_path.exists():
            idx_path.unlink()

        # Should rebuild automatically
        idx = store.get_index(pr_meta.pr_session_id)
        assert len(idx.runs) == 1

    def test_rebuild_picks_up_new_run(self, store: FilePRSessionStore, pr_meta: PRSessionMeta):
        r1 = store.create_run(pr_meta.pr_session_id, "ctx-1", "aaa", "bbb")

        # Manually delete index and add a new run
        from backend.storage.pr_session.paths import pr_index_file
        idx_path = pr_index_file(store._storage_dir, pr_meta.pr_key)
        if idx_path.exists():
            idx_path.unlink()

        r2 = store.create_run(pr_meta.pr_session_id, "ctx-2", "aaa", "ccc")

        idx = store.rebuild_index(pr_meta.pr_session_id)
        assert len(idx.runs) == 2


# ---------------------------------------------------------------------------
# Deleted runs
# ---------------------------------------------------------------------------


class TestDeletedRuns:
    def test_index_after_run_directory_removed(self, store: FilePRSessionStore, pr_meta: PRSessionMeta):
        r1 = store.create_run(pr_meta.pr_session_id, "ctx-1", "aaa", "bbb")
        r2 = store.create_run(pr_meta.pr_session_id, "ctx-2", "aaa", "ccc")

        # Remove r1's directory
        import shutil
        from backend.storage.pr_session.paths import run_dir
        r1_dir = run_dir(store._storage_dir, pr_meta.pr_key, r1.run_id)
        shutil.rmtree(r1_dir)

        # Rebuild index
        idx = store.rebuild_index(pr_meta.pr_session_id)
        assert len(idx.runs) == 1
        assert idx.runs[0].run_id == r2.run_id


# ---------------------------------------------------------------------------
# Finding lookup
# ---------------------------------------------------------------------------


class TestFindingLookup:
    def test_find_findings(self, store: FilePRSessionStore, pr_meta: PRSessionMeta):
        r1 = store.create_run(pr_meta.pr_session_id, "ctx-1", "aaa", "bbb")
        store.update_run_state(r1.run_id, lifecycle=RunLifecycle.COMPLETED)

        # Save a result with findings
        result = ResultRecord(
            run_id=r1.run_id,
            pr_session_id=pr_meta.pr_session_id,
            lifecycle="completed",
            findings=[
                {"title": "Bug A", "severity": "critical", "files": ["a.py"]},
                {"title": "Bug B", "severity": "warning", "files": ["b.py"]},
            ],
        )
        store.save_result(result)

        # Find all findings
        findings = store.find_findings(pr_meta.pr_session_id)
        assert len(findings) == 2

    def test_find_findings_filter_severity(self, store: FilePRSessionStore, pr_meta: PRSessionMeta):
        r1 = store.create_run(pr_meta.pr_session_id, "ctx-1", "aaa", "bbb")
        store.update_run_state(r1.run_id, lifecycle=RunLifecycle.COMPLETED)

        result = ResultRecord(
            run_id=r1.run_id,
            pr_session_id=pr_meta.pr_session_id,
            lifecycle="completed",
            findings=[
                {"title": "Bug A", "severity": "critical", "files": ["a.py"]},
                {"title": "Bug B", "severity": "warning", "files": ["b.py"]},
            ],
        )
        store.save_result(result)

        findings = store.find_findings(pr_meta.pr_session_id, severity="critical")
        assert len(findings) == 1
        assert findings[0]["title"] == "Bug A"

    def test_find_findings_filter_head_sha(self, store: FilePRSessionStore, pr_meta: PRSessionMeta):
        r1 = store.create_run(pr_meta.pr_session_id, "ctx-1", "aaa", "bbb")
        r2 = store.create_run(pr_meta.pr_session_id, "ctx-2", "aaa", "ccc")
        store.update_run_state(r1.run_id, lifecycle=RunLifecycle.COMPLETED)
        store.update_run_state(r2.run_id, lifecycle=RunLifecycle.COMPLETED)

        store.save_result(ResultRecord(
            run_id=r1.run_id, pr_session_id=pr_meta.pr_session_id,
            lifecycle="completed", findings=[{"title": "A", "severity": "warning"}],
        ))
        store.save_result(ResultRecord(
            run_id=r2.run_id, pr_session_id=pr_meta.pr_session_id,
            lifecycle="completed", findings=[{"title": "B", "severity": "warning"}],
        ))

        findings = store.find_findings(pr_meta.pr_session_id, head_sha="bbb")
        assert len(findings) == 1
        assert findings[0]["title"] == "A"
        assert findings[0]["run_id"] == r1.run_id

    def test_find_findings_limit(self, store: FilePRSessionStore, pr_meta: PRSessionMeta):
        r1 = store.create_run(pr_meta.pr_session_id, "ctx-1", "aaa", "bbb")
        store.update_run_state(r1.run_id, lifecycle=RunLifecycle.COMPLETED)

        store.save_result(ResultRecord(
            run_id=r1.run_id, pr_session_id=pr_meta.pr_session_id,
            lifecycle="completed",
            findings=[{"title": f"Bug {i}", "severity": "warning"} for i in range(10)],
        ))

        findings = store.find_findings(pr_meta.pr_session_id, limit=3)
        assert len(findings) == 3

    def test_find_findings_includes_run_metadata(self, store: FilePRSessionStore, pr_meta: PRSessionMeta):
        r1 = store.create_run(pr_meta.pr_session_id, "ctx-1", "aaa", "bbb")
        store.update_run_state(r1.run_id, lifecycle=RunLifecycle.COMPLETED)

        store.save_result(ResultRecord(
            run_id=r1.run_id, pr_session_id=pr_meta.pr_session_id,
            lifecycle="completed",
            findings=[{"title": "Bug", "severity": "critical"}],
        ))

        findings = store.find_findings(pr_meta.pr_session_id)
        assert len(findings) == 1
        assert findings[0]["run_id"] == r1.run_id
        assert findings[0]["head_sha"] == "bbb"
