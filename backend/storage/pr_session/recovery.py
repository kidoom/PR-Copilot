"""Startup recovery for persisted PR sessions and review runs.

On backend startup, the recovery module scans persisted PR sessions and
reconstructs ``RunManager`` entries for terminal (completed/failed/cancelled)
runs.  Non-terminal runs (queued/running/cancelling) are atomically
transitioned to ``interrupted``.

Recovery never automatically repeats model calls, workspace preparation,
or tool execution.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.storage.pr_session.models import (
    PRSessionIndex,
    RunLifecycle,
    RunState,
)
from backend.storage.pr_session.store import (
    FilePRSessionStore,
    UnsupportedSchemaError,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Recovery result
# ---------------------------------------------------------------------------

class RecoveryReport:
    """Summary of what startup recovery did."""

    def __init__(self) -> None:
        self.pr_sessions_scanned: int = 0
        self.runs_scanned: int = 0
        self.terminal_runs_restored: int = 0
        self.interrupted_runs: int = 0
        self.corrupt_runs_skipped: int = 0
        self.indexes_rebuilt: int = 0
        self.errors: list[str] = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "pr_sessions_scanned": self.pr_sessions_scanned,
            "runs_scanned": self.runs_scanned,
            "terminal_runs_restored": self.terminal_runs_restored,
            "interrupted_runs": self.interrupted_runs,
            "corrupt_runs_skipped": self.corrupt_runs_skipped,
            "indexes_rebuilt": self.indexes_rebuilt,
            "errors": self.errors,
        }


# ---------------------------------------------------------------------------
# Recovery logic
# ---------------------------------------------------------------------------


def recover_on_startup(
    store: FilePRSessionStore,
    run_manager: Any,
) -> RecoveryReport:
    """Scan all PR sessions and recover persisted run state into RunManager.

    This function is called once during backend startup.  It:

    1. Scans all PR sessions under the sessions root.
    2. For each run, reads the authoritative ``run.json``.
    3. Terminal runs (completed/failed/cancelled) restore their state into
       the ``RunManager`` so status queries and WebSocket replay work.
    4. Non-terminal runs (queued/running/cancelling) are atomically
       transitioned to ``interrupted``.
    5. Missing or corrupt indexes are rebuilt.
    6. No model calls, workspace operations, or tool execution are triggered.

    Parameters
    ----------
    store:
        The filesystem-backed PR session store.
    run_manager:
        The in-memory ``RunManager`` instance to restore into.

    Returns
    -------
    RecoveryReport
        Summary of recovered and interrupted runs.
    """
    report = RecoveryReport()

    sessions_root = store._sessions_root
    if not sessions_root.exists():
        logger.info("No sessions directory found at %s, skipping recovery", sessions_root)
        return report

    for pr_dir in sorted(sessions_root.iterdir()):
        if not pr_dir.is_dir():
            continue

        # Load PR metadata
        pr_meta_file = pr_dir / "pr.json"
        if not pr_meta_file.exists():
            continue

        try:
            import json
            raw = json.loads(pr_meta_file.read_text(encoding="utf-8"))
            from backend.storage.pr_session.models import PRSessionMeta
            pr_meta = PRSessionMeta.from_dict(raw)
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning("Skipping corrupt PR session at %s: %s", pr_dir, exc)
            report.errors.append(f"Corrupt pr.json at {pr_dir}: {exc}")
            continue

        report.pr_sessions_scanned += 1
        pr_session_id = pr_meta.pr_session_id

        # Rebuild index if needed
        try:
            idx = store.get_index(pr_session_id)
            if not (pr_dir / "index.json").exists():
                report.indexes_rebuilt += 1
        except Exception as exc:
            logger.warning("Failed to load/rebuild index for %s: %s", pr_session_id, exc)
            report.errors.append(f"Index error for {pr_session_id}: {exc}")
            continue

        # Process each run
        runs_dir = pr_dir / "runs"
        if not runs_dir.exists():
            continue

        for run_dir in sorted(runs_dir.iterdir()):
            if not run_dir.is_dir():
                continue

            run_json = run_dir / "run.json"
            if not run_json.exists():
                continue

            try:
                raw = json.loads(run_json.read_text(encoding="utf-8"))
                run_state = RunState.from_dict(raw)
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                logger.warning("Skipping corrupt run at %s: %s", run_dir, exc)
                report.corrupt_runs_skipped += 1
                report.errors.append(f"Corrupt run.json at {run_dir}: {exc}")
                continue

            report.runs_scanned += 1

            if run_state.lifecycle in (
                RunLifecycle.COMPLETED,
                RunLifecycle.FAILED,
                RunLifecycle.CANCELLED,
            ):
                _restore_terminal_run(store, run_manager, run_state, report)
            else:
                _interrupt_run(store, run_state, report)

    logger.info(
        "Recovery complete: %s", report.to_dict()
    )
    return report


def _restore_terminal_run(
    store: FilePRSessionStore,
    run_manager: Any,
    run_state: RunState,
    report: RecoveryReport,
) -> None:
    """Restore a terminal run into the RunManager.

    If a persisted ``context.json`` exists for this run and the schema version
    is compatible, the ``PRContext`` is hydrated into the in-memory context
    manager so that context-dependent code paths (e.g. inspection endpoints
    that need ``get_context()``) work after a restart.
    """
    from backend.agent.runtime.events import AgentRunSession, RunStatus

    # Map durable lifecycle to in-memory status
    lifecycle_to_status = {
        RunLifecycle.COMPLETED: RunStatus.COMPLETED,
        RunLifecycle.FAILED: RunStatus.FAILED,
        RunLifecycle.CANCELLED: RunStatus.CANCELLED,
    }
    status = lifecycle_to_status.get(run_state.lifecycle)
    if status is None:
        return

    # Create in-memory session
    session = AgentRunSession(
        run_id=run_state.run_id,
        context_id=run_state.context_id,
        status=status,
    )

    # Restore final result if available
    if run_state.lifecycle == RunLifecycle.COMPLETED:
        result_record = store.load_result(run_state.run_id)
        if result_record:
            session.final_result = {
                "findings": result_record.findings,
                "coverage": result_record.coverage,
                "usage": result_record.usage,
            }
    elif run_state.lifecycle == RunLifecycle.FAILED:
        session.error_summary = run_state.error_summary

    # Restore retained events (up to the cap)
    persisted_events = store.load_events(run_state.run_id, limit=200)
    from backend.agent.runtime.events import RunEvent
    for pe in persisted_events:
        event = RunEvent(
            run_id=pe.run_id,
            type=pe.event_type,
            payload=pe.payload,
            event_id=pe.event_id,
            sequence=pe.sequence,
            created_at=pe.created_at,
        )
        session.retained_events.append(event)

    # Set the next sequence
    if persisted_events:
        session._next_sequence = max(e.sequence for e in persisted_events) + 1

    # Hydrate PRContext from persisted context.json if available
    _hydrate_context(store, run_state, report)

    # Register with RunManager
    run_manager._runs[run_state.run_id] = session
    report.terminal_runs_restored += 1


def _hydrate_context(
    store: FilePRSessionStore,
    run_state: RunState,
    report: RecoveryReport,
) -> None:
    """Load persisted context.json and register it in the in-memory context manager.

    If the context file is missing, corrupt, or uses an unsupported schema
    version, the error is logged and recorded in the report but does not
    prevent the run from being restored.
    """
    from backend.domain.pr_context.context_manager import _contexts
    from backend.storage.pr_session.context_serializer import deserialize_context

    try:
        ctx_record = store.load_context(run_state.run_id)
    except UnsupportedSchemaError as exc:
        logger.warning(
            "Context for run %s uses unsupported schema v%s (max v%s), skipping hydration",
            run_state.run_id, exc.stored_version, exc.max_supported_version,
        )
        report.errors.append(
            f"Context for run {run_state.run_id}: unsupported schema v{exc.stored_version}"
        )
        return
    except Exception as exc:
        logger.debug("Could not load context for run %s: %s", run_state.run_id, exc)
        return

    if ctx_record is None:
        return

    try:
        ctx = deserialize_context(ctx_record)
        _contexts[ctx.context_id] = ctx
    except Exception as exc:
        logger.warning(
            "Failed to hydrate context for run %s: %s", run_state.run_id, exc
        )
        report.errors.append(f"Context hydration failed for {run_state.run_id}: {exc}")


def _interrupt_run(
    store: FilePRSessionStore,
    run_state: RunState,
    report: RecoveryReport,
) -> None:
    """Transition a non-terminal run to interrupted."""
    try:
        store.update_run_state(
            run_state.run_id,
            lifecycle=RunLifecycle.INTERRUPTED,
            completed_at=_now_iso(),
        )
        report.interrupted_runs += 1
    except Exception as exc:
        logger.warning("Failed to interrupt run %s: %s", run_state.run_id, exc)
        report.errors.append(f"Failed to interrupt {run_state.run_id}: {exc}")


# ---------------------------------------------------------------------------
# Retry support
# ---------------------------------------------------------------------------


def create_retry_run(
    store: FilePRSessionStore,
    original_run_id: str,
    context_id: str,
) -> RunState | None:
    """Create a new run that references an interrupted run.

    Returns the new ``RunState`` or ``None`` if the original run is not
    in an interruptible state.
    """
    try:
        original = store.get_run_state(original_run_id)
    except Exception:
        return None

    if original.lifecycle not in (
        RunLifecycle.INTERRUPTED,
        RunLifecycle.FAILED,
    ):
        return None

    ps_id = store.resolve_run_to_pr_session(original_run_id)
    if ps_id is None:
        return None

    new_state = store.create_run(
        ps_id,
        context_id,
        original.base_sha,
        original.head_sha,
        retry_of_run_id=original_run_id,
    )
    return new_state
