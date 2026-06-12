"""Optional importer for existing agent memory sessions.

Scans the agent memory directory, groups sessions by ``run_id``, and
creates ``AgentSessionsRecord`` references for runs that already exist
in the PR session store.  Unmatched memory sessions are left untouched.

This is a one-time migration helper; it is safe to run repeatedly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from backend.agent.runtime.memory.models import MemorySessionMeta
from backend.agent.runtime.memory.store import FileMemoryStore
from backend.storage.pr_session.models import (
    AgentSessionRef,
    AgentSessionsRecord,
    AgentSessionStatus,
)
from backend.storage.pr_session.store import FilePRSessionStore

logger = logging.getLogger(__name__)


@dataclass
class ImportReport:
    """Summary of what the memory session import did."""

    memory_sessions_scanned: int = 0
    sessions_with_run_id: int = 0
    runs_matched: int = 0
    runs_imported: int = 0
    sessions_unmatched: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_sessions_scanned": self.memory_sessions_scanned,
            "sessions_with_run_id": self.sessions_with_run_id,
            "runs_matched": self.runs_matched,
            "runs_imported": self.runs_imported,
            "sessions_unmatched": self.sessions_unmatched,
            "errors": self.errors,
        }


def import_memory_sessions(
    memory_store: FileMemoryStore,
    pr_store: FilePRSessionStore,
) -> ImportReport:
    """Import existing agent memory sessions into the PR session store.

    For each ``run_id`` found in memory sessions:
    1. Check if the run exists in the PR session store.
    2. If it does, create an ``AgentSessionsRecord`` with references to all
       memory sessions for that run.
    3. If a record already exists, skip (idempotent).
    4. Memory sessions without a matching run are left untouched.

    Parameters
    ----------
    memory_store:
        The agent memory store to scan.
    pr_store:
        The PR session store to write references into.

    Returns
    -------
    ImportReport
        Summary of scanned, matched, and imported sessions.
    """
    report = ImportReport()

    # Scan all memory sessions
    all_sessions = memory_store.list_sessions()
    report.memory_sessions_scanned = len(all_sessions)

    # Group by run_id
    by_run_id: dict[str, list[MemorySessionMeta]] = {}
    for meta in all_sessions:
        if meta.run_id:
            by_run_id.setdefault(meta.run_id, []).append(meta)
            report.sessions_with_run_id += 1
        else:
            report.sessions_unmatched += 1

    # Process each run_id
    for run_id, sessions in by_run_id.items():
        # Check if this run exists in the PR session store
        ps_id = pr_store.resolve_run_to_pr_session(run_id)
        if ps_id is None:
            report.sessions_unmatched += len(sessions)
            continue

        report.runs_matched += 1

        # Check if agent-sessions.json already exists
        existing = pr_store.load_agent_sessions(run_id)
        if existing is not None:
            # Already imported, skip
            logger.debug("Run %s already has agent sessions record, skipping", run_id)
            continue

        # Build AgentSessionRef entries
        refs: list[AgentSessionRef] = []
        for meta in sessions:
            ref = AgentSessionRef(
                memory_session_id=meta.session_id,
                agent_kind=meta.agent_kind.value,
                agent_type=meta.agent_type,
                status=AgentSessionStatus.ACTIVE,
                task_id=meta.task_id,
                created_at=meta.created_at.isoformat(),
            )
            refs.append(ref)

        # Create and save the record
        record = AgentSessionsRecord(
            run_id=run_id,
            sessions=refs,
        )
        try:
            pr_store.save_agent_sessions(record)
            report.runs_imported += 1
        except Exception as exc:
            logger.warning("Failed to save agent sessions for run %s: %s", run_id, exc)
            report.errors.append(f"Failed to save agent sessions for {run_id}: {exc}")

    logger.info("Memory session import complete: %s", report.to_dict())
    return report
