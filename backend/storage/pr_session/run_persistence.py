"""Hooks for persisting RunManager lifecycle transitions and events.

This module bridges the in-memory ``RunManager`` and the durable
``FilePRSessionStore``.  It is designed to be called from the existing
run creation and execution paths without replacing the RunManager itself.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from backend.storage.pr_session.models import (
    AgentSessionsRecord,
    ContextRecord,
    PersistedEvent,
    PRSessionMeta,
    ResultRecord,
    RunLifecycle,
    RunState,
    TaskPlanRecord,
)

if TYPE_CHECKING:
    from backend.agent.runtime.events import RunEvent
    from backend.storage.pr_session.store import FilePRSessionStore

logger = logging.getLogger(__name__)

# Mapping from in-memory RunStatus string values to durable RunLifecycle
_STATUS_TO_LIFECYCLE: dict[str, RunLifecycle] = {
    "queued": RunLifecycle.QUEUED,
    "running": RunLifecycle.RUNNING,
    "cancelling": RunLifecycle.CANCELLING,
    "cancelled": RunLifecycle.CANCELLED,
    "completed": RunLifecycle.COMPLETED,
    "failed": RunLifecycle.FAILED,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Run creation
# ---------------------------------------------------------------------------


def create_durable_run(
    store: FilePRSessionStore,
    owner: str,
    repo: str,
    pull_number: int,
    context_id: str,
    base_sha: str,
    head_sha: str,
    *,
    retry_of_run_id: str | None = None,
) -> tuple[PRSessionMeta, RunState]:
    """Create a PR session (if needed) and a durable run record.

    Returns ``(pr_meta, run_state)``.
    """
    pr_meta = store.get_or_create_pr_session(owner, repo, pull_number)
    run_state = store.create_run(
        pr_meta.pr_session_id,
        context_id,
        base_sha,
        head_sha,
        retry_of_run_id=retry_of_run_id,
    )
    return pr_meta, run_state


# ---------------------------------------------------------------------------
# Lifecycle transitions
# ---------------------------------------------------------------------------


def persist_lifecycle_transition(
    store: FilePRSessionStore,
    run_id: str,
    status: Any,
    *,
    error_summary: str | None = None,
) -> None:
    """Atomically persist a RunManager lifecycle transition.

    ``status`` can be a ``RunStatus`` enum or its string value.
    """
    status_val = status.value if hasattr(status, "value") else str(status)
    lifecycle = _STATUS_TO_LIFECYCLE.get(status_val)
    if lifecycle is None:
        logger.warning("Unknown RunStatus %s, skipping persistence", status)
        return

    kwargs: dict[str, Any] = {"lifecycle": lifecycle}
    if lifecycle == RunLifecycle.RUNNING:
        kwargs["started_at"] = _now_iso()
    if lifecycle in (
        RunLifecycle.COMPLETED,
        RunLifecycle.FAILED,
        RunLifecycle.CANCELLED,
        RunLifecycle.INTERRUPTED,
    ):
        kwargs["completed_at"] = _now_iso()
    if error_summary is not None:
        kwargs["error_summary"] = error_summary

    try:
        store.update_run_state(run_id, **kwargs)
    except Exception:
        logger.warning("Failed to persist lifecycle for run %s", run_id, exc_info=True)


def persist_interrupted(store: FilePRSessionStore, run_id: str) -> None:
    """Mark a run as interrupted (used during startup recovery)."""
    try:
        store.update_run_state(
            run_id,
            lifecycle=RunLifecycle.INTERRUPTED,
            completed_at=_now_iso(),
        )
    except Exception:
        logger.warning("Failed to mark run %s as interrupted", run_id, exc_info=True)


# ---------------------------------------------------------------------------
# Event persistence
# ---------------------------------------------------------------------------


def persist_event(store: FilePRSessionStore, event: Any) -> None:
    """Append a RunManager event to the durable events.jsonl.

    ``event`` is a ``RunEvent`` instance but typed as ``Any`` to avoid
    circular imports with ``backend.agent.runtime.events``.
    """
    try:
        persisted = PersistedEvent(
            event_id=event.event_id,
            run_id=event.run_id,
            sequence=event.sequence,
            event_type=event.type,
            created_at=event.created_at,
            payload=event.payload,
        )
        store.append_event(persisted)
    except Exception:
        logger.warning(
            "Failed to persist event %s for run %s",
            event.event_id,
            event.run_id,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Context persistence
# ---------------------------------------------------------------------------


def persist_context(
    store: FilePRSessionStore,
    run_id: str,
    context_record: ContextRecord,
) -> None:
    """Save a context record under the owning run."""
    try:
        store.save_context_for_run(run_id, context_record)
    except Exception:
        logger.warning("Failed to persist context for run %s", run_id, exc_info=True)


# ---------------------------------------------------------------------------
# Task plan persistence
# ---------------------------------------------------------------------------


def persist_task_plan(
    store: FilePRSessionStore,
    run_id: str,
    task_plan_record: TaskPlanRecord,
) -> None:
    """Save a task plan under the owning run."""
    try:
        store.save_task_plan_for_run(run_id, task_plan_record)
    except Exception:
        logger.warning("Failed to persist task plan for run %s", run_id, exc_info=True)


# ---------------------------------------------------------------------------
# Result persistence
# ---------------------------------------------------------------------------


def persist_result(
    store: FilePRSessionStore,
    run_id: str,
    pr_session_id: str,
    lifecycle: str,
    *,
    findings: list[dict[str, Any]] | None = None,
    error_summary: str | None = None,
    coverage: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
) -> None:
    """Persist a terminal result record."""
    try:
        record = ResultRecord(
            run_id=run_id,
            pr_session_id=pr_session_id,
            lifecycle=lifecycle,
            findings=findings or [],
            error_summary=error_summary,
            coverage=coverage or {},
            usage=usage or {},
        )
        store.save_result(record)
    except Exception:
        logger.warning("Failed to persist result for run %s", run_id, exc_info=True)


# ---------------------------------------------------------------------------
# Agent session reference persistence
# ---------------------------------------------------------------------------


def persist_agent_sessions(
    store: FilePRSessionStore,
    run_id: str,
    record: AgentSessionsRecord,
) -> None:
    """Save agent session references under the owning run."""
    try:
        store.save_agent_sessions(record)
    except Exception:
        logger.warning(
            "Failed to persist agent sessions for run %s", run_id, exc_info=True
        )


# ---------------------------------------------------------------------------
# Sequence recovery
# ---------------------------------------------------------------------------


def restore_next_sequence(store: FilePRSessionStore, run_id: str) -> int:
    """Return the next sequence number for a run based on persisted events."""
    max_seq = store.get_max_sequence(run_id)
    return max_seq + 1
