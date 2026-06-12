"""Tests for the inspection API routes.

Covers: pagination, filters, unknown ids, recovered records,
unsupported schemas, and bounded responses.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

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
def client(store: FilePRSessionStore):
    from unittest.mock import MagicMock

    mock_deps = MagicMock()
    mock_deps.pr_session_store = store

    # Patch in every module that imports get_agent_deps at the top level
    with patch("backend.api.routes.inspection.get_agent_deps", return_value=mock_deps), \
         patch("backend.api.routes.review_runs.get_agent_deps", return_value=mock_deps):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.api.routes.inspection import router as inspection_router
        from backend.api.routes.review_runs import router as review_runs_router

        app = FastAPI()
        app.include_router(inspection_router)
        app.include_router(review_runs_router)
        yield TestClient(app)


@pytest.fixture
def pr_with_runs(store: FilePRSessionStore) -> tuple[PRSessionMeta, RunState, RunState]:
    meta = store.get_or_create_pr_session("octocat", "hello-world", 42)
    r1 = store.create_run(meta.pr_session_id, "ctx-1", "aaa", "bbb")
    r2 = store.create_run(meta.pr_session_id, "ctx-2", "aaa", "ccc")
    store.update_run_state(r1.run_id, lifecycle=RunLifecycle.COMPLETED)
    store.update_run_state(r2.run_id, lifecycle=RunLifecycle.FAILED, error_summary="timeout")
    store.save_result(ResultRecord(
        run_id=r1.run_id, pr_session_id=meta.pr_session_id,
        lifecycle="completed",
        findings=[{"title": "Bug A", "severity": "critical"}],
    ))
    return meta, r1, r2


# ---------------------------------------------------------------------------
# PR session routes
# ---------------------------------------------------------------------------


class TestPRSessionRoutes:
    def test_get_pr_session(self, client, store: FilePRSessionStore):
        store.get_or_create_pr_session("octocat", "hello-world", 42)
        resp = client.get("/api/inspection/pr-sessions/octocat/hello-world/42")
        assert resp.status_code == 200
        data = resp.json()
        assert data["owner"] == "octocat"
        assert data["pull_number"] == 42

    def test_get_pr_session_not_found(self, client):
        resp = client.get("/api/inspection/pr-sessions/nobody/nothing/999")
        assert resp.status_code == 404

    def test_list_runs(self, client, pr_with_runs):
        meta, r1, r2 = pr_with_runs
        resp = client.get(f"/api/inspection/pr-sessions/octocat/hello-world/42/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["runs"]) == 2

    def test_list_runs_with_limit(self, client, pr_with_runs):
        meta, r1, r2 = pr_with_runs
        resp = client.get(
            f"/api/inspection/pr-sessions/octocat/hello-world/42/runs?limit=1"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["runs"]) == 1

    def test_list_runs_not_found(self, client):
        resp = client.get("/api/inspection/pr-sessions/nobody/nothing/999/runs")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Run detail routes
# ---------------------------------------------------------------------------


class TestRunDetailRoutes:
    def test_get_run_detail(self, client, pr_with_runs):
        meta, r1, r2 = pr_with_runs
        resp = client.get(f"/api/inspection/runs/{r1.run_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == r1.run_id
        assert data["lifecycle"] == "completed"
        assert "result" in data

    def test_get_run_detail_with_result(self, client, pr_with_runs):
        meta, r1, r2 = pr_with_runs
        resp = client.get(f"/api/inspection/runs/{r1.run_id}")
        data = resp.json()
        assert "result" in data
        assert len(data["result"]["findings"]) == 1

    def test_get_run_detail_not_found(self, client):
        resp = client.get("/api/inspection/runs/nonexistent")
        assert resp.status_code == 404

    def test_get_run_events(self, client, pr_with_runs, store: FilePRSessionStore):
        meta, r1, r2 = pr_with_runs
        from backend.storage.pr_session.models import PersistedEvent
        store.append_event(PersistedEvent(
            event_id="e0", run_id=r1.run_id,
            sequence=0, event_type="run.started", created_at="t0",
        ))
        store.append_event(PersistedEvent(
            event_id="e1", run_id=r1.run_id,
            sequence=1, event_type="run.completed", created_at="t1",
        ))

        resp = client.get(f"/api/inspection/runs/{r1.run_id}/events")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2

    def test_get_run_events_with_limit(self, client, pr_with_runs, store: FilePRSessionStore):
        meta, r1, r2 = pr_with_runs
        from backend.storage.pr_session.models import PersistedEvent
        for i in range(5):
            store.append_event(PersistedEvent(
                event_id=f"e{i}", run_id=r1.run_id,
                sequence=i, event_type="tool.call", created_at=f"t{i}",
            ))

        resp = client.get(f"/api/inspection/runs/{r1.run_id}/events?limit=2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2

    def test_get_run_agent_sessions(self, client, pr_with_runs, store: FilePRSessionStore):
        meta, r1, r2 = pr_with_runs
        from backend.storage.pr_session.models import AgentSessionRef, AgentSessionsRecord, AgentSessionStatus
        store.save_agent_sessions(AgentSessionsRecord(
            run_id=r1.run_id,
            sessions=[AgentSessionRef(
                memory_session_id="main-abc",
                agent_kind="main",
                agent_type="main-agent",
                status=AgentSessionStatus.COMPLETED,
            )],
        ))

        resp = client.get(f"/api/inspection/runs/{r1.run_id}/agent-sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["sessions"]) == 1

    def test_get_run_agent_sessions_not_found(self, client, pr_with_runs):
        meta, r1, r2 = pr_with_runs
        resp = client.get(f"/api/inspection/runs/{r1.run_id}/agent-sessions")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Finding search routes
# ---------------------------------------------------------------------------


class TestFindingSearchRoutes:
    def test_search_findings(self, client, pr_with_runs):
        meta, r1, r2 = pr_with_runs
        resp = client.get(
            f"/api/inspection/pr-sessions/octocat/hello-world/42/findings"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["findings"][0]["title"] == "Bug A"

    def test_search_findings_by_severity(self, client, pr_with_runs):
        meta, r1, r2 = pr_with_runs
        resp = client.get(
            f"/api/inspection/pr-sessions/octocat/hello-world/42/findings?severity=critical"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1

    def test_search_findings_severity_no_match(self, client, pr_with_runs):
        meta, r1, r2 = pr_with_runs
        resp = client.get(
            f"/api/inspection/pr-sessions/octocat/hello-world/42/findings?severity=info"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0

    def test_search_findings_not_found(self, client):
        resp = client.get(
            "/api/inspection/pr-sessions/nobody/nothing/999/findings"
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Existing run status route with recovered runs
# ---------------------------------------------------------------------------


class TestRecoveredRunStatus:
    def test_recovered_completed_run(self, client, pr_with_runs):
        meta, r1, r2 = pr_with_runs
        resp = client.get(f"/api/review/runs/{r1.run_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert "final_result" in data

    def test_recovered_failed_run(self, client, pr_with_runs):
        meta, r1, r2 = pr_with_runs
        resp = client.get(f"/api/review/runs/{r2.run_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failed"
        assert data["error_summary"] == "timeout"

    def test_run_not_found(self, client):
        resp = client.get("/api/review/runs/nonexistent")
        assert resp.status_code == 404

    def test_interrupted_run_has_flag(self, client, store: FilePRSessionStore):
        meta = store.get_or_create_pr_session("octocat", "hello", 42)
        rs = store.create_run(meta.pr_session_id, "ctx-1", "aaa", "bbb")
        store.update_run_state(
            rs.run_id, lifecycle=RunLifecycle.INTERRUPTED, completed_at="t",
        )

        resp = client.get(f"/api/review/runs/{rs.run_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "interrupted"
        assert data.get("interrupted") is True
