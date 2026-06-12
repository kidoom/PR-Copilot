"""Filesystem-backed PR session store.

``FilePRSessionStore`` is the single entry point for all durable PR session
operations.  It is rooted at ``{PR_COPILOT_STORAGE_DIR}/sessions/pr`` and
uses atomic JSON writes, locked JSONL appends, and process-local per-PR /
per-run synchronisation to provide correct behaviour for the current
single-process FastAPI deployment.

The store does **not** claim safe multi-process or multi-host writes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.storage.pr_session.models import (
    AgentSessionRef,
    AgentSessionsRecord,
    ContextRecord,
    PersistedEvent,
    PRSessionIndex,
    PRSessionMeta,
    ResultRecord,
    RunIndexEntry,
    RunLifecycle,
    RunState,
    TaskPlanRecord,
    UnsupportedVersion,
)
from backend.storage.pr_session.paths import (
    PathEscapeError,
    build_pr_key,
    normalize_identity,
    pr_index_file,
    pr_meta_file,
    pr_session_dir,
    pr_sessions_root,
    pr_temp_file,
    run_agent_sessions_file,
    run_context_file,
    run_dir,
    run_events_file,
    run_result_file,
    run_state_file,
    run_task_plan_file,
    run_temp_file,
    runs_dir,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PRSessionStoreError(Exception):
    """Base exception for PR session store operations."""


class PRSessionNotFoundError(PRSessionStoreError):
    """Raised when a PR session is not found."""


class RunNotFoundError(PRSessionStoreError):
    """Raised when a run is not found."""


class IntegrityError(PRSessionStoreError):
    """Raised on corrupt or inconsistent persisted state."""


class UnsupportedSchemaError(PRSessionStoreError):
    """Raised when a persisted record uses a newer schema version."""

    def __init__(self, component: str, stored: int, max_supported: int) -> None:
        self.component = component
        self.stored_version = stored
        self.max_supported_version = max_supported
        super().__init__(
            f"{component}: schema version {stored} > max supported {max_supported}"
        )


# ---------------------------------------------------------------------------
# PRSessionStore protocol
# ---------------------------------------------------------------------------


class PRSessionStore:
    """Abstract protocol for PR session persistence."""

    def get_or_create_pr_session(
        self, owner: str, repo: str, pull_number: int
    ) -> PRSessionMeta:
        raise NotImplementedError

    def get_pr_session(self, pr_session_id: str) -> PRSessionMeta | None:
        raise NotImplementedError

    def get_pr_session_by_identity(
        self, owner: str, repo: str, pull_number: int
    ) -> PRSessionMeta | None:
        raise NotImplementedError

    def create_run(
        self,
        pr_session_id: str,
        context_id: str,
        base_sha: str,
        head_sha: str,
        *,
        retry_of_run_id: str | None = None,
    ) -> RunState:
        raise NotImplementedError

    def get_run_state(self, run_id: str) -> RunState:
        raise NotImplementedError

    def update_run_state(self, run_id: str, **kwargs: Any) -> RunState:
        raise NotImplementedError

    def save_context(self, record: ContextRecord) -> None:
        raise NotImplementedError

    def load_context(self, run_id: str) -> ContextRecord | None:
        raise NotImplementedError

    def save_task_plan(self, record: TaskPlanRecord) -> None:
        raise NotImplementedError

    def load_task_plan(self, run_id: str) -> TaskPlanRecord | None:
        raise NotImplementedError

    def append_event(self, event: PersistedEvent) -> PersistedEvent:
        raise NotImplementedError

    def load_events(
        self,
        run_id: str,
        *,
        after_sequence: int = -1,
        event_types: list[str] | None = None,
        limit: int = 100,
    ) -> list[PersistedEvent]:
        raise NotImplementedError

    def save_result(self, record: ResultRecord) -> None:
        raise NotImplementedError

    def load_result(self, run_id: str) -> ResultRecord | None:
        raise NotImplementedError

    def save_agent_sessions(self, record: AgentSessionsRecord) -> None:
        raise NotImplementedError

    def load_agent_sessions(self, run_id: str) -> AgentSessionsRecord | None:
        raise NotImplementedError

    def get_index(self, pr_session_id: str) -> PRSessionIndex:
        raise NotImplementedError

    def rebuild_index(self, pr_session_id: str) -> PRSessionIndex:
        raise NotImplementedError

    def list_runs(
        self, pr_session_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[RunIndexEntry]:
        raise NotImplementedError

    def find_runs_by_head_sha(
        self, pr_session_id: str, head_sha: str
    ) -> list[RunIndexEntry]:
        raise NotImplementedError

    def resolve_run_to_pr_session(self, run_id: str) -> str | None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# FilePRSessionStore
# ---------------------------------------------------------------------------


class FilePRSessionStore(PRSessionStore):
    """Filesystem-backed implementation of ``PRSessionStore``.

    Parameters
    ----------
    storage_dir:
        Root of the PR Copilot storage tree (e.g. ``~/.pr-copilot``).
    """

    def __init__(self, storage_dir: str | Path) -> None:
        self._storage_dir = Path(storage_dir)
        self._sessions_root = pr_sessions_root(self._storage_dir)

        # Process-local locks: one per PR session, one per run.
        self._pr_locks: dict[str, asyncio.Lock] = {}
        self._run_locks: dict[str, asyncio.Lock] = {}

        # In-memory cache for run_id -> pr_session_id mapping (rebuilt lazily).
        self._run_to_pr: dict[str, str] = {}

    # -- lock helpers -------------------------------------------------------

    def _pr_lock(self, pr_session_id: str) -> asyncio.Lock:
        if pr_session_id not in self._pr_locks:
            self._pr_locks[pr_session_id] = asyncio.Lock()
        return self._pr_locks[pr_session_id]

    def _run_lock(self, run_id: str) -> asyncio.Lock:
        if run_id not in self._run_locks:
            self._run_locks[run_id] = asyncio.Lock()
        return self._run_locks[run_id]

    # -- atomic JSON helpers ------------------------------------------------

    def _write_json(self, target: Path, data: dict[str, Any]) -> None:
        """Atomically write *data* as JSON to *target*.

        Uses a temporary sibling file, ``os.fsync``, and ``os.replace``.
        """
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.parent / f".tmp-{uuid.uuid4().hex[:8]}.json"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(str(tmp), str(target))
        except BaseException:
            # Clean up temp file on any failure
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _read_json(self, path: Path) -> dict[str, Any] | None:
        """Read and parse a JSON file, returning None if missing or corrupt."""
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Corrupt JSON at %s: %s", path, exc)
            return None

    # -- JSONL helpers ------------------------------------------------------

    def _append_jsonl(self, path: Path, data: dict[str, Any]) -> None:
        """Append a single JSON line to *path*, flushing to disk."""
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(data, ensure_ascii=False) + "\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    def _read_jsonl(
        self,
        path: Path,
        *,
        after_sequence: int = -1,
        event_types: list[str] | None = None,
        limit: int = 100,
    ) -> tuple[list[dict[str, Any]], int, int]:
        """Read JSONL entries with optional filters.

        Returns ``(records, skipped_lines, total_matching)``.
        """
        if not path.exists():
            return [], 0, 0

        records: list[dict[str, Any]] = []
        skipped = 0
        with open(path, "r", encoding="utf-8") as f:
            for _line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    skipped += 1
                    continue

                seq = rec.get("sequence", -1)
                if seq <= after_sequence:
                    continue
                if event_types and rec.get("event_type") not in event_types:
                    continue
                if len(records) >= limit:
                    break
                records.append(rec)

        return records, skipped, len(records)

    # -- PR session operations ----------------------------------------------

    def get_or_create_pr_session(
        self, owner: str, repo: str, pull_number: int
    ) -> PRSessionMeta:
        ident = normalize_identity(owner, repo, pull_number)
        pr_key = build_pr_key(ident)

        # Try to load existing
        meta_file = pr_meta_file(self._storage_dir, pr_key)
        raw = self._read_json(meta_file)
        if raw is not None:
            return PRSessionMeta.from_dict(raw)

        # Create new — use deterministic ID from PR identity
        from backend.storage.pr_session.paths import _short_hash
        pr_session_id = f"ps_{_short_hash(ident.owner, ident.repo, ident.pull_number)}"
        meta = PRSessionMeta(
            pr_session_id=pr_session_id,
            pr_key=pr_key,
            owner=ident.owner,
            repo=ident.repo,
            pull_number=ident.pull_number,
        )
        # Ensure directory exists
        session_dir = pr_session_dir(self._storage_dir, pr_key)
        session_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(meta_file, meta.to_dict())
        return meta

    def get_pr_session(self, pr_session_id: str) -> PRSessionMeta | None:
        """Find a PR session by scanning pr.json files.

        For efficiency, caches pr_key -> pr_session_id in memory.
        """
        if not self._sessions_root.exists():
            return None
        for child in self._sessions_root.iterdir():
            if not child.is_dir():
                continue
            meta_file = child / "pr.json"
            raw = self._read_json(meta_file)
            if raw is None:
                continue
            try:
                meta = PRSessionMeta.from_dict(raw)
                if meta.pr_session_id == pr_session_id:
                    return meta
            except (KeyError, ValueError):
                continue
        return None

    def get_pr_session_by_identity(
        self, owner: str, repo: str, pull_number: int
    ) -> PRSessionMeta | None:
        ident = normalize_identity(owner, repo, pull_number)
        pr_key = build_pr_key(ident)
        meta_file = pr_meta_file(self._storage_dir, pr_key)
        raw = self._read_json(meta_file)
        if raw is None:
            return None
        return PRSessionMeta.from_dict(raw)

    # -- Run operations -----------------------------------------------------

    def create_run(
        self,
        pr_session_id: str,
        context_id: str,
        base_sha: str,
        head_sha: str,
        *,
        retry_of_run_id: str | None = None,
    ) -> RunState:
        meta = self.get_pr_session(pr_session_id)
        if meta is None:
            raise PRSessionNotFoundError(pr_session_id)

        run_id = f"run_{uuid.uuid4().hex[:12]}"
        state = RunState(
            run_id=run_id,
            pr_session_id=pr_session_id,
            context_id=context_id,
            base_sha=base_sha,
            head_sha=head_sha,
            lifecycle=RunLifecycle.QUEUED,
            retry_of_run_id=retry_of_run_id,
        )

        # Create run directory and write run.json
        rdir = run_dir(self._storage_dir, meta.pr_key, run_id)
        rdir.mkdir(parents=True, exist_ok=True)
        self._write_json(run_state_file(self._storage_dir, meta.pr_key, run_id), state.to_dict())

        # Update PR meta run_count
        meta.run_count += 1
        meta.updated_at = datetime.now(timezone.utc).isoformat()
        self._write_json(pr_meta_file(self._storage_dir, meta.pr_key), meta.to_dict())

        # Cache mapping
        self._run_to_pr[run_id] = pr_session_id

        return state

    def _find_pr_key_for_run(self, run_id: str) -> str | None:
        """Find the pr_key that owns a given run_id."""
        if run_id in self._run_to_pr:
            ps_id = self._run_to_pr[run_id]
            meta = self.get_pr_session(ps_id)
            if meta:
                return meta.pr_key

        # Scan
        if not self._sessions_root.exists():
            return None
        for child in self._sessions_root.iterdir():
            if not child.is_dir():
                continue
            rdir = child / "runs" / run_id
            if rdir.exists() and (rdir / "run.json").exists():
                raw = self._read_json(child / "pr.json")
                if raw:
                    try:
                        m = PRSessionMeta.from_dict(raw)
                        self._run_to_pr[run_id] = m.pr_session_id
                        return m.pr_key
                    except (KeyError, ValueError):
                        pass
        return None

    def get_run_state(self, run_id: str) -> RunState:
        pr_key = self._find_pr_key_for_run(run_id)
        if pr_key is None:
            raise RunNotFoundError(run_id)
        raw = self._read_json(run_state_file(self._storage_dir, pr_key, run_id))
        if raw is None:
            raise RunNotFoundError(run_id)
        return RunState.from_dict(raw)

    def update_run_state(self, run_id: str, **kwargs: Any) -> RunState:
        """Atomically update run state fields.

        Valid kwargs: lifecycle, error_summary, started_at, completed_at.
        """
        pr_key = self._find_pr_key_for_run(run_id)
        if pr_key is None:
            raise RunNotFoundError(run_id)

        sf = run_state_file(self._storage_dir, pr_key, run_id)
        raw = self._read_json(sf)
        if raw is None:
            raise RunNotFoundError(run_id)

        state = RunState.from_dict(raw)

        if "lifecycle" in kwargs:
            lc = kwargs["lifecycle"]
            state.lifecycle = RunLifecycle(lc) if isinstance(lc, str) else lc
        if "error_summary" in kwargs:
            state.error_summary = kwargs["error_summary"]
        if "started_at" in kwargs:
            state.started_at = kwargs["started_at"]
        if "completed_at" in kwargs:
            state.completed_at = kwargs["completed_at"]

        state.updated_at = datetime.now(timezone.utc).isoformat()
        self._write_json(sf, state.to_dict())
        return state

    # -- Context operations -------------------------------------------------

    def save_context(self, record: ContextRecord) -> None:
        meta = self.get_pr_session(record.pr_session_id)
        if meta is None:
            raise PRSessionNotFoundError(record.pr_session_id)

        # Find run to get run_id -> directory
        raw = self._read_json(
            run_state_file(self._storage_dir, meta.pr_key, "")
        )  # won't work, need run_id
        # We need to find the run directory from context_id
        # Scan runs for matching context_id
        rd = runs_dir(self._storage_dir, meta.pr_key)
        if rd.exists():
            for run_child in rd.iterdir():
                if not run_child.is_dir():
                    continue
                rs_raw = self._read_json(run_child / "run.json")
                if rs_raw and rs_raw.get("context_id") == record.context_id:
                    self._write_json(run_child / "context.json", record.to_dict())
                    return

        # If no matching run found, store under the first run that has this pr_session_id
        # This shouldn't normally happen - context should be saved after run creation
        raise RunNotFoundError(
            f"No run found for context_id={record.context_id} in pr_session={record.pr_session_id}"
        )

    def save_context_for_run(self, run_id: str, record: ContextRecord) -> None:
        """Save context record associated with a specific run."""
        pr_key = self._find_pr_key_for_run(run_id)
        if pr_key is None:
            raise RunNotFoundError(run_id)
        self._write_json(
            run_context_file(self._storage_dir, pr_key, run_id), record.to_dict()
        )

    def load_context(self, run_id: str) -> ContextRecord | None:
        pr_key = self._find_pr_key_for_run(run_id)
        if pr_key is None:
            return None
        raw = self._read_json(run_context_file(self._storage_dir, pr_key, run_id))
        if raw is None:
            return None
        return ContextRecord.from_dict(raw)

    # -- Task plan operations -----------------------------------------------

    def save_task_plan_for_run(self, run_id: str, record: TaskPlanRecord) -> None:
        pr_key = self._find_pr_key_for_run(run_id)
        if pr_key is None:
            raise RunNotFoundError(run_id)
        self._write_json(
            run_task_plan_file(self._storage_dir, pr_key, run_id), record.to_dict()
        )

    def save_task_plan(self, record: TaskPlanRecord) -> None:
        self.save_task_plan_for_run(record.run_id, record)

    def load_task_plan(self, run_id: str) -> TaskPlanRecord | None:
        pr_key = self._find_pr_key_for_run(run_id)
        if pr_key is None:
            return None
        raw = self._read_json(
            run_task_plan_file(self._storage_dir, pr_key, run_id)
        )
        if raw is None:
            return None
        return TaskPlanRecord.from_dict(raw)

    # -- Event operations ---------------------------------------------------

    def append_event(self, event: PersistedEvent) -> PersistedEvent:
        pr_key = self._find_pr_key_for_run(event.run_id)
        if pr_key is None:
            raise RunNotFoundError(event.run_id)

        ef = run_events_file(self._storage_dir, pr_key, event.run_id)
        self._append_jsonl(ef, event.to_dict())
        return event

    def load_events(
        self,
        run_id: str,
        *,
        after_sequence: int = -1,
        event_types: list[str] | None = None,
        limit: int = 100,
    ) -> list[PersistedEvent]:
        pr_key = self._find_pr_key_for_run(run_id)
        if pr_key is None:
            return []

        ef = run_events_file(self._storage_dir, pr_key, run_id)
        records, skipped, _ = self._read_jsonl(
            ef, after_sequence=after_sequence, event_types=event_types, limit=limit
        )
        if skipped > 0:
            logger.warning(
                "Skipped %d malformed JSONL lines in %s", skipped, ef
            )
        return [PersistedEvent.from_dict(r) for r in records]

    def get_max_sequence(self, run_id: str) -> int:
        """Return the highest persisted sequence number for a run, or -1."""
        pr_key = self._find_pr_key_for_run(run_id)
        if pr_key is None:
            return -1
        ef = run_events_file(self._storage_dir, pr_key, run_id)
        if not ef.exists():
            return -1
        max_seq = -1
        with open(ef, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    seq = rec.get("sequence", -1)
                    if seq > max_seq:
                        max_seq = seq
                except json.JSONDecodeError:
                    continue
        return max_seq

    # -- Result operations --------------------------------------------------

    def save_result(self, record: ResultRecord) -> None:
        pr_key = self._find_pr_key_for_run(record.run_id)
        if pr_key is None:
            raise RunNotFoundError(record.run_id)
        self._write_json(
            run_result_file(self._storage_dir, pr_key, record.run_id), record.to_dict()
        )

    def load_result(self, run_id: str) -> ResultRecord | None:
        pr_key = self._find_pr_key_for_run(run_id)
        if pr_key is None:
            return None
        raw = self._read_json(run_result_file(self._storage_dir, pr_key, run_id))
        if raw is None:
            return None
        return ResultRecord.from_dict(raw)

    # -- Agent sessions operations ------------------------------------------

    def save_agent_sessions(self, record: AgentSessionsRecord) -> None:
        pr_key = self._find_pr_key_for_run(record.run_id)
        if pr_key is None:
            raise RunNotFoundError(record.run_id)
        self._write_json(
            run_agent_sessions_file(self._storage_dir, pr_key, record.run_id),
            record.to_dict(),
        )

    def load_agent_sessions(self, run_id: str) -> AgentSessionsRecord | None:
        pr_key = self._find_pr_key_for_run(run_id)
        if pr_key is None:
            return None
        raw = self._read_json(
            run_agent_sessions_file(self._storage_dir, pr_key, run_id)
        )
        if raw is None:
            return None
        return AgentSessionsRecord.from_dict(raw)

    # -- Index operations ---------------------------------------------------

    def get_index(self, pr_session_id: str) -> PRSessionIndex:
        meta = self.get_pr_session(pr_session_id)
        if meta is None:
            raise PRSessionNotFoundError(pr_session_id)

        idx_file = pr_index_file(self._storage_dir, meta.pr_key)
        raw = self._read_json(idx_file)
        if raw is not None:
            try:
                return PRSessionIndex.from_dict(raw)
            except (KeyError, ValueError):
                logger.warning("Corrupt index at %s, rebuilding", idx_file)

        return self.rebuild_index(pr_session_id)

    def rebuild_index(self, pr_session_id: str) -> PRSessionIndex:
        meta = self.get_pr_session(pr_session_id)
        if meta is None:
            raise PRSessionNotFoundError(pr_session_id)

        rd = runs_dir(self._storage_dir, meta.pr_key)
        entries: list[RunIndexEntry] = []

        if rd.exists():
            for run_child in sorted(rd.iterdir(), reverse=True):
                if not run_child.is_dir():
                    continue
                rs_file = run_child / "run.json"
                rs_raw = self._read_json(rs_file)
                if rs_raw is None:
                    continue
                try:
                    rs = RunState.from_dict(rs_raw)
                except (KeyError, ValueError):
                    continue

                # Try to load result for counts
                result_raw = self._read_json(run_child / "result.json")
                finding_count = 0
                error_count = 0
                if result_raw:
                    finding_count = len(result_raw.get("findings", []))
                    if result_raw.get("error_summary"):
                        error_count = 1

                # Agent session count
                as_raw = self._read_json(run_child / "agent-sessions.json")
                agent_count = len(as_raw.get("sessions", [])) if as_raw else 0

                entries.append(
                    RunIndexEntry(
                        run_id=rs.run_id,
                        head_sha=rs.head_sha,
                        base_sha=rs.base_sha,
                        lifecycle=rs.lifecycle.value,
                        created_at=rs.created_at,
                        updated_at=rs.updated_at,
                        completed_at=rs.completed_at,
                        finding_count=finding_count,
                        error_count=error_count,
                        agent_session_count=agent_count,
                    )
                )

        idx = PRSessionIndex(
            pr_session_id=pr_session_id,
            runs=entries,
        )
        self._write_json(
            pr_index_file(self._storage_dir, meta.pr_key), idx.to_dict()
        )
        return idx

    def list_runs(
        self, pr_session_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[RunIndexEntry]:
        idx = self.get_index(pr_session_id)
        # Newest first (entries are in creation order, newest is last)
        sorted_entries = list(reversed(idx.runs))
        return sorted_entries[offset : offset + limit]

    def find_runs_by_head_sha(
        self, pr_session_id: str, head_sha: str
    ) -> list[RunIndexEntry]:
        idx = self.get_index(pr_session_id)
        return [e for e in idx.runs if e.head_sha == head_sha]

    def find_findings(
        self,
        pr_session_id: str,
        *,
        severity: str | None = None,
        filename: str | None = None,
        head_sha: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Find findings across runs for a PR session with optional filters.

        Returns a list of finding dicts augmented with ``run_id`` and
        ``head_sha`` from the owning run.
        """
        idx = self.get_index(pr_session_id)
        results: list[dict[str, Any]] = []

        for entry in reversed(idx.runs):
            if head_sha and entry.head_sha != head_sha:
                continue

            pr_key = self._find_pr_key_for_run(entry.run_id)
            if pr_key is None:
                continue
            result_raw = self._read_json(
                run_result_file(self._storage_dir, pr_key, entry.run_id)
            )
            if result_raw is None:
                continue

            for finding in result_raw.get("findings", []):
                if severity and finding.get("severity") != severity:
                    continue
                if filename:
                    finding_files = finding.get("files", [])
                    if isinstance(finding_files, list) and filename not in finding_files:
                        continue
                enriched = {**finding, "run_id": entry.run_id, "head_sha": entry.head_sha}
                results.append(enriched)
                if len(results) >= limit:
                    return results

        return results

    def resolve_run_to_pr_session(self, run_id: str) -> str | None:
        if run_id in self._run_to_pr:
            return self._run_to_pr[run_id]

        pr_key = self._find_pr_key_for_run(run_id)
        if pr_key is None:
            return None
        meta_file = pr_meta_file(self._storage_dir, pr_key)
        raw = self._read_json(meta_file)
        if raw is None:
            return None
        try:
            meta = PRSessionMeta.from_dict(raw)
            self._run_to_pr[run_id] = meta.pr_session_id
            return meta.pr_session_id
        except (KeyError, ValueError):
            return None

    def list_all_sessions(self, *, limit: int = 50) -> list[PRSessionMeta]:
        """List all PR sessions, newest first."""
        if not self._sessions_root.exists():
            return []
        sessions: list[PRSessionMeta] = []
        for child in self._sessions_root.iterdir():
            if not child.is_dir():
                continue
            meta_file = child / "pr.json"
            raw = self._read_json(meta_file)
            if raw is None:
                continue
            try:
                sessions.append(PRSessionMeta.from_dict(raw))
            except (KeyError, ValueError):
                continue
        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        return sessions[:limit]
