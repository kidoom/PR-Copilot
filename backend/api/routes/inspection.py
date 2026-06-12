"""Read-only inspection API routes for PR sessions and review runs.

These routes provide structured access to persisted PR session data
without modifying it.  They are thin adapters over the
``FilePRSessionStore`` operations.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.deps import get_agent_deps

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/inspection", tags=["inspection"])


# ---------------------------------------------------------------------------
# PR session routes
# ---------------------------------------------------------------------------


@router.get("/pr-sessions/{owner}/{repo}/{pull_number}")
async def get_pr_session(owner: str, repo: str, pull_number: int) -> dict[str, Any]:
    """Resolve a PR session by identity and return its metadata."""
    store = get_agent_deps().pr_session_store
    meta = store.get_pr_session_by_identity(owner, repo, pull_number)
    if meta is None:
        raise HTTPException(status_code=404, detail="PR session not found")
    return meta.to_dict()


@router.get("/pr-sessions/{owner}/{repo}/{pull_number}/runs")
async def list_runs(
    owner: str,
    repo: str,
    pull_number: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List runs for a PR session, newest first."""
    store = get_agent_deps().pr_session_store
    meta = store.get_pr_session_by_identity(owner, repo, pull_number)
    if meta is None:
        raise HTTPException(status_code=404, detail="PR session not found")

    runs = store.list_runs(meta.pr_session_id, limit=limit, offset=offset)
    return {
        "pr_session_id": meta.pr_session_id,
        "runs": [r.to_dict() for r in runs],
        "total": len(store.get_index(meta.pr_session_id).runs),
    }


# ---------------------------------------------------------------------------
# Run detail routes
# ---------------------------------------------------------------------------


@router.get("/runs/{run_id}")
async def get_run_detail(run_id: str) -> dict[str, Any]:
    """Get persisted run details including state, result, and agent sessions."""
    store = get_agent_deps().pr_session_store

    try:
        state = store.get_run_state(run_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Run not found")

    result = store.load_result(run_id)
    agent_sessions = store.load_agent_sessions(run_id)

    d = state.to_dict()
    if result:
        d["result"] = result.to_dict()
    if agent_sessions:
        d["agent_sessions"] = agent_sessions.to_dict()
    return d


@router.get("/runs/{run_id}/events")
async def get_run_events(
    run_id: str,
    after_sequence: int = Query(default=-1, ge=-1),
    event_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """Get persisted events for a run with pagination."""
    store = get_agent_deps().pr_session_store

    event_types = [event_type] if event_type else None
    events = store.load_events(
        run_id,
        after_sequence=after_sequence,
        event_types=event_types,
        limit=limit,
    )
    return {
        "run_id": run_id,
        "events": [e.to_dict() for e in events],
        "count": len(events),
    }


@router.get("/runs/{run_id}/agent-sessions")
async def get_run_agent_sessions(run_id: str) -> dict[str, Any]:
    """Get agent session references for a run."""
    store = get_agent_deps().pr_session_store
    record = store.load_agent_sessions(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Agent sessions not found")
    return record.to_dict()


# ---------------------------------------------------------------------------
# Finding search routes
# ---------------------------------------------------------------------------


@router.get("/pr-sessions/{owner}/{repo}/{pull_number}/findings")
async def search_findings(
    owner: str,
    repo: str,
    pull_number: int,
    severity: str | None = Query(default=None),
    filename: str | None = Query(default=None),
    head_sha: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Search findings across runs for a PR session."""
    store = get_agent_deps().pr_session_store
    meta = store.get_pr_session_by_identity(owner, repo, pull_number)
    if meta is None:
        raise HTTPException(status_code=404, detail="PR session not found")

    findings = store.find_findings(
        meta.pr_session_id,
        severity=severity,
        filename=filename,
        head_sha=head_sha,
        limit=limit,
    )
    return {
        "pr_session_id": meta.pr_session_id,
        "findings": findings,
        "count": len(findings),
    }
