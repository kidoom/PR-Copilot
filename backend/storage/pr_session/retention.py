"""Retention policy enforcement and integrity scanning.

Provides configurable, reference-aware cleanup for PR runs, contexts,
events, results, and agent memory.  Never deletes active runs.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from backend.storage.pr_session.models import (
    IntegrityFinding,
    IntegrityLevel,
    IntegrityReport,
    PRSessionIndex,
    RetentionPolicy,
    RunLifecycle,
)
from backend.storage.pr_session.store import FilePRSessionStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------


def load_retention_policy_from_env() -> RetentionPolicy:
    """Load retention policy from environment variables.

    Environment variables (all in days, empty/unset = keep forever):
    - PR_COPILOT_RETENTION_RESULT_DAYS
    - PR_COPILOT_RETENTION_EVENT_DAYS
    - PR_COPILOT_RETENTION_CONTEXT_DAYS
    - PR_COPILOT_RETENTION_TRANSCRIPT_DAYS
    - PR_COPILOT_RETENTION_EMPTY_SESSION_DAYS
    """

    def _parse_days(env_var: str, default: int | None) -> int | None:
        val = os.environ.get(env_var, "")
        if not val:
            return default
        try:
            return int(val)
        except ValueError:
            logger.warning("Invalid %s value: %s, using default", env_var, val)
            return default

    return RetentionPolicy(
        result_days=_parse_days("PR_COPILOT_RETENTION_RESULT_DAYS", 90),
        event_days=_parse_days("PR_COPILOT_RETENTION_EVENT_DAYS", 30),
        context_days=_parse_days("PR_COPILOT_RETENTION_CONTEXT_DAYS", 14),
        agent_transcript_days=_parse_days("PR_COPILOT_RETENTION_TRANSCRIPT_DAYS", 7),
        empty_session_days=_parse_days("PR_COPILOT_RETENTION_EMPTY_SESSION_DAYS", 1),
    )


# ---------------------------------------------------------------------------
# Cleanup planning
# ---------------------------------------------------------------------------


class CleanupPlan:
    """Dry-run cleanup plan that reports what would be deleted."""

    def __init__(self) -> None:
        self.files: list[dict[str, Any]] = []
        self.estimated_bytes: int = 0
        self.skipped_active: list[str] = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "files": self.files,
            "estimated_bytes": self.estimated_bytes,
            "file_count": len(self.files),
            "skipped_active_count": len(self.skipped_active),
            "skipped_active": self.skipped_active,
        }


def plan_cleanup(
    store: FilePRSessionStore,
    policy: RetentionPolicy,
) -> CleanupPlan:
    """Plan what would be deleted without actually deleting anything.

    Excludes active (queued/running/cancelling) runs.
    """
    plan = CleanupPlan()
    now = datetime.now(timezone.utc)

    sessions_root = store._sessions_root
    if not sessions_root.exists():
        return plan

    for pr_dir in sessions_root.iterdir():
        if not pr_dir.is_dir():
            continue

        runs_dir = pr_dir / "runs"
        if not runs_dir.exists():
            continue

        for run_dir in runs_dir.iterdir():
            if not run_dir.is_dir():
                continue

            run_json = run_dir / "run.json"
            if not run_json.exists():
                continue

            try:
                raw = json.loads(run_json.read_text(encoding="utf-8"))
                lifecycle = raw.get("lifecycle", "")
                created_at = raw.get("created_at", "")
            except (json.JSONDecodeError, OSError):
                continue

            # Skip active runs
            if lifecycle in ("queued", "running", "cancelling"):
                plan.skipped_active.append(run_dir.name)
                continue

            # Parse creation time
            try:
                run_time = datetime.fromisoformat(created_at)
            except (ValueError, TypeError):
                continue

            age_days = (now - run_time).days

            # Check events
            events_file = run_dir / "events.jsonl"
            if events_file.exists() and policy.event_days is not None:
                if age_days > policy.event_days:
                    size = events_file.stat().st_size
                    plan.files.append({
                        "path": str(events_file),
                        "type": "events",
                        "age_days": age_days,
                        "bytes": size,
                    })
                    plan.estimated_bytes += size

            # Check context
            context_file = run_dir / "context.json"
            if context_file.exists() and policy.context_days is not None:
                if age_days > policy.context_days:
                    size = context_file.stat().st_size
                    plan.files.append({
                        "path": str(context_file),
                        "type": "context",
                        "age_days": age_days,
                        "bytes": size,
                    })
                    plan.estimated_bytes += size

            # Check result (longer retention)
            result_file = run_dir / "result.json"
            if result_file.exists() and policy.result_days is not None:
                if age_days > policy.result_days:
                    size = result_file.stat().st_size
                    plan.files.append({
                        "path": str(result_file),
                        "type": "result",
                        "age_days": age_days,
                        "bytes": size,
                    })
                    plan.estimated_bytes += size

    return plan


# ---------------------------------------------------------------------------
# Integrity scanning
# ---------------------------------------------------------------------------


def scan_integrity(
    store: FilePRSessionStore,
    pr_session_id: str,
) -> IntegrityReport:
    """Scan a PR session for integrity issues."""
    report = IntegrityReport(pr_session_id=pr_session_id)

    meta = store.get_pr_session(pr_session_id)
    if meta is None:
        report.findings.append(IntegrityFinding(
            level=IntegrityLevel.ERROR,
            component="pr.json",
            message=f"PR session {pr_session_id} not found",
        ))
        return report

    rd = store._storage_dir / "sessions" / "pr" / meta.pr_key / "runs"
    if not rd.exists():
        return report

    for run_dir in rd.iterdir():
        if not run_dir.is_dir():
            continue

        run_id = run_dir.name
        run_json = run_dir / "run.json"

        # Check run.json
        if not run_json.exists():
            report.findings.append(IntegrityFinding(
                level=IntegrityLevel.ERROR,
                component="run.json",
                message="Missing run.json",
                run_id=run_id,
            ))
            continue

        try:
            raw = json.loads(run_json.read_text(encoding="utf-8"))
            run_state = store.get_run_state(run_id)
        except (json.JSONDecodeError, KeyError, ValueError):
            report.findings.append(IntegrityFinding(
                level=IntegrityLevel.ERROR,
                component="run.json",
                message="Corrupt run.json",
                run_id=run_id,
            ))
            continue

        # Check for non-terminal active runs
        if run_state.lifecycle in (RunLifecycle.QUEUED, RunLifecycle.RUNNING, RunLifecycle.CANCELLING):
            report.findings.append(IntegrityFinding(
                level=IntegrityLevel.WARNING,
                component="run.json",
                message=f"Non-terminal lifecycle: {run_state.lifecycle.value}",
                run_id=run_id,
            ))

        # Check events.jsonl
        events_file = run_dir / "events.jsonl"
        if events_file.exists():
            try:
                with open(events_file, "r", encoding="utf-8") as f:
                    for line_num, line in enumerate(f, 1):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            json.loads(line)
                        except json.JSONDecodeError:
                            report.findings.append(IntegrityFinding(
                                level=IntegrityLevel.WARNING,
                                component="events.jsonl",
                                message=f"Malformed line {line_num}",
                                run_id=run_id,
                            ))

                # Check for sequence gaps
                events = store.load_events(run_id, limit=10000)
                if events:
                    sequences = [e.sequence for e in events]
                    expected = list(range(sequences[0], sequences[-1] + 1))
                    if sequences != expected:
                        report.findings.append(IntegrityFinding(
                            level=IntegrityLevel.WARNING,
                            component="events.jsonl",
                            message="Sequence gap detected",
                            run_id=run_id,
                        ))
            except OSError:
                pass

        # Check agent-sessions.json
        agent_file = run_dir / "agent-sessions.json"
        if agent_file.exists():
            try:
                raw = json.loads(agent_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                report.findings.append(IntegrityFinding(
                    level=IntegrityLevel.WARNING,
                    component="agent-sessions.json",
                    message="Corrupt agent-sessions.json",
                    run_id=run_id,
                ))

    # Check index
    idx_file = store._storage_dir / "sessions" / "pr" / meta.pr_key / "index.json"
    if idx_file.exists():
        try:
            raw = json.loads(idx_file.read_text(encoding="utf-8"))
            idx = PRSessionIndex.from_dict(raw)
        except (json.JSONDecodeError, KeyError, ValueError):
            report.findings.append(IntegrityFinding(
                level=IntegrityLevel.WARNING,
                component="index.json",
                message="Corrupt index, will be rebuilt on next access",
            ))
    else:
        report.findings.append(IntegrityFinding(
            level=IntegrityLevel.WARNING,
            component="index.json",
            message="Missing index, will be rebuilt on next access",
        ))

    return report
