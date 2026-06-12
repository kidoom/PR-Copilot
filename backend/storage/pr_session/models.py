"""Versioned durable models for PR session persistence.

Each JSON/JSONL document includes a ``schema_version`` field. Readers must
check the version before deserializing and return a structured
``UnsupportedVersion`` result when the stored version is newer than the
highest version this module understands.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Schema version constants
# ---------------------------------------------------------------------------

PR_SESSION_SCHEMA_VERSION = 1
RUN_STATE_SCHEMA_VERSION = 1
PERSISTED_EVENT_SCHEMA_VERSION = 1
RUN_INDEX_SCHEMA_VERSION = 1
AGENT_SESSION_REF_SCHEMA_VERSION = 1
CONTEXT_RECORD_SCHEMA_VERSION = 1
TASK_PLAN_RECORD_SCHEMA_VERSION = 1
RESULT_RECORD_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class RunLifecycle(str, Enum):
    """Durable run lifecycle states.

    Extends the in-memory ``RunStatus`` with an ``INTERRUPTED`` state for
    runs that were active when the backend terminated.
    """

    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class AgentSessionStatus(str, Enum):
    """Lifecycle status for a referenced agent memory session."""

    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INVALID = "invalid"
    MAX_STEP = "max_step"


class IntegrityLevel(str, Enum):
    """Severity of an integrity finding."""

    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


# ---------------------------------------------------------------------------
# PR identity
# ---------------------------------------------------------------------------


@dataclass
class PRIdentity:
    """Canonical pull request identity."""

    owner: str
    repo: str
    pull_number: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "repo": self.repo,
            "pull_number": self.pull_number,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PRIdentity:
        return cls(
            owner=data["owner"],
            repo=data["repo"],
            pull_number=int(data["pull_number"]),
        )


# ---------------------------------------------------------------------------
# PR session metadata (pr.json)
# ---------------------------------------------------------------------------


@dataclass
class PRSessionMeta:
    """Top-level metadata persisted as ``pr.json``."""

    pr_session_id: str
    pr_key: str
    owner: str
    repo: str
    pull_number: int
    schema_version: int = PR_SESSION_SCHEMA_VERSION
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    run_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "pr_session_id": self.pr_session_id,
            "pr_key": self.pr_key,
            "owner": self.owner,
            "repo": self.repo,
            "pull_number": self.pull_number,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "run_count": self.run_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PRSessionMeta:
        return cls(
            schema_version=data.get("schema_version", 1),
            pr_session_id=data["pr_session_id"],
            pr_key=data["pr_key"],
            owner=data["owner"],
            repo=data["repo"],
            pull_number=int(data["pull_number"]),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            run_count=data.get("run_count", 0),
        )


# ---------------------------------------------------------------------------
# Run state (run.json)
# ---------------------------------------------------------------------------


@dataclass
class RunState:
    """Durable lifecycle state persisted as ``run.json``."""

    run_id: str
    pr_session_id: str
    context_id: str
    base_sha: str
    head_sha: str
    lifecycle: RunLifecycle = RunLifecycle.QUEUED
    schema_version: int = RUN_STATE_SCHEMA_VERSION
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    started_at: str | None = None
    completed_at: str | None = None
    retry_of_run_id: str | None = None
    error_summary: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "pr_session_id": self.pr_session_id,
            "context_id": self.context_id,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "lifecycle": self.lifecycle.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.started_at is not None:
            d["started_at"] = self.started_at
        if self.completed_at is not None:
            d["completed_at"] = self.completed_at
        if self.retry_of_run_id is not None:
            d["retry_of_run_id"] = self.retry_of_run_id
        if self.error_summary is not None:
            d["error_summary"] = self.error_summary
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunState:
        return cls(
            schema_version=data.get("schema_version", 1),
            run_id=data["run_id"],
            pr_session_id=data["pr_session_id"],
            context_id=data["context_id"],
            base_sha=data["base_sha"],
            head_sha=data["head_sha"],
            lifecycle=RunLifecycle(data["lifecycle"]),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            retry_of_run_id=data.get("retry_of_run_id"),
            error_summary=data.get("error_summary"),
        )


# ---------------------------------------------------------------------------
# Persisted event (events.jsonl)
# ---------------------------------------------------------------------------


@dataclass
class PersistedEvent:
    """One operational event appended to ``events.jsonl``."""

    event_id: str
    run_id: str
    sequence: int
    event_type: str
    created_at: str
    schema_version: int = PERSISTED_EVENT_SCHEMA_VERSION
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "event_type": self.event_type,
            "created_at": self.created_at,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PersistedEvent:
        return cls(
            schema_version=data.get("schema_version", 1),
            event_id=data["event_id"],
            run_id=data["run_id"],
            sequence=int(data["sequence"]),
            event_type=data["event_type"],
            created_at=data["created_at"],
            payload=data.get("payload", {}),
        )


# ---------------------------------------------------------------------------
# Run index entry (index.json)
# ---------------------------------------------------------------------------


@dataclass
class RunIndexEntry:
    """One entry in the per-PR ``index.json`` run list."""

    run_id: str
    head_sha: str
    base_sha: str
    lifecycle: str
    created_at: str
    updated_at: str
    completed_at: str | None = None
    finding_count: int = 0
    error_count: int = 0
    agent_session_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "run_id": self.run_id,
            "head_sha": self.head_sha,
            "base_sha": self.base_sha,
            "lifecycle": self.lifecycle,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "finding_count": self.finding_count,
            "error_count": self.error_count,
            "agent_session_count": self.agent_session_count,
        }
        if self.completed_at is not None:
            d["completed_at"] = self.completed_at
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunIndexEntry:
        return cls(
            run_id=data["run_id"],
            head_sha=data["head_sha"],
            base_sha=data["base_sha"],
            lifecycle=data["lifecycle"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            completed_at=data.get("completed_at"),
            finding_count=data.get("finding_count", 0),
            error_count=data.get("error_count", 0),
            agent_session_count=data.get("agent_session_count", 0),
        )


@dataclass
class PRSessionIndex:
    """The full ``index.json`` document."""

    schema_version: int = RUN_INDEX_SCHEMA_VERSION
    pr_session_id: str = ""
    runs: list[RunIndexEntry] = field(default_factory=list)
    rebuilt_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "pr_session_id": self.pr_session_id,
            "runs": [r.to_dict() for r in self.runs],
            "rebuilt_at": self.rebuilt_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PRSessionIndex:
        return cls(
            schema_version=data.get("schema_version", 1),
            pr_session_id=data.get("pr_session_id", ""),
            runs=[RunIndexEntry.from_dict(r) for r in data.get("runs", [])],
            rebuilt_at=data.get("rebuilt_at", ""),
        )


# ---------------------------------------------------------------------------
# Agent session reference (agent-sessions.json)
# ---------------------------------------------------------------------------


@dataclass
class AgentSessionRef:
    """Reference to an existing agent memory session."""

    memory_session_id: str
    agent_kind: str  # "main" or "subagent"
    agent_type: str
    status: AgentSessionStatus = AgentSessionStatus.ACTIVE
    schema_version: int = AGENT_SESSION_REF_SCHEMA_VERSION
    task_id: str = ""
    child_session_id: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "schema_version": self.schema_version,
            "memory_session_id": self.memory_session_id,
            "agent_kind": self.agent_kind,
            "agent_type": self.agent_type,
            "status": self.status.value,
            "created_at": self.created_at,
        }
        if self.task_id:
            d["task_id"] = self.task_id
        if self.child_session_id:
            d["child_session_id"] = self.child_session_id
        if self.completed_at is not None:
            d["completed_at"] = self.completed_at
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentSessionRef:
        return cls(
            schema_version=data.get("schema_version", 1),
            memory_session_id=data["memory_session_id"],
            agent_kind=data["agent_kind"],
            agent_type=data["agent_type"],
            status=AgentSessionStatus(data["status"]),
            task_id=data.get("task_id", ""),
            child_session_id=data.get("child_session_id", ""),
            created_at=data["created_at"],
            completed_at=data.get("completed_at"),
        )


@dataclass
class AgentSessionsRecord:
    """The full ``agent-sessions.json`` document."""

    schema_version: int = AGENT_SESSION_REF_SCHEMA_VERSION
    run_id: str = ""
    sessions: list[AgentSessionRef] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "sessions": [s.to_dict() for s in self.sessions],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentSessionsRecord:
        return cls(
            schema_version=data.get("schema_version", 1),
            run_id=data.get("run_id", ""),
            sessions=[AgentSessionRef.from_dict(s) for s in data.get("sessions", [])],
        )


# ---------------------------------------------------------------------------
# Context record (context.json)
# ---------------------------------------------------------------------------


@dataclass
class ContextRecord:
    """Persisted PR context sufficient for restart-time status."""

    context_id: str
    pr_session_id: str
    owner: str
    repo: str
    pull_number: int
    base_sha: str
    head_sha: str
    schema_version: int = CONTEXT_RECORD_SCHEMA_VERSION
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    # Bounded serialized PR metadata, files, derived signals
    pr_metadata: dict[str, Any] = field(default_factory=dict)
    commits: dict[str, Any] = field(default_factory=dict)
    files: list[dict[str, Any]] = field(default_factory=list)
    derived: dict[str, Any] = field(default_factory=dict)
    # Bounded patch content (truncated)
    patch_content: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "context_id": self.context_id,
            "pr_session_id": self.pr_session_id,
            "owner": self.owner,
            "repo": self.repo,
            "pull_number": self.pull_number,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "created_at": self.created_at,
            "pr_metadata": self.pr_metadata,
            "commits": self.commits,
            "files": self.files,
            "derived": self.derived,
            "patch_content": self.patch_content,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextRecord:
        return cls(
            schema_version=data.get("schema_version", 1),
            context_id=data["context_id"],
            pr_session_id=data["pr_session_id"],
            owner=data["owner"],
            repo=data["repo"],
            pull_number=int(data["pull_number"]),
            base_sha=data["base_sha"],
            head_sha=data["head_sha"],
            created_at=data["created_at"],
            pr_metadata=data.get("pr_metadata", {}),
            commits=data.get("commits", {}),
            files=data.get("files", []),
            derived=data.get("derived", {}),
            patch_content=data.get("patch_content", {}),
        )


# ---------------------------------------------------------------------------
# Task plan record (task-plan.json)
# ---------------------------------------------------------------------------


@dataclass
class TaskPlanRecord:
    """Persisted task plan for a run."""

    run_id: str
    pr_session_id: str
    schema_version: int = TASK_PLAN_RECORD_SCHEMA_VERSION
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    tasks: list[dict[str, Any]] = field(default_factory=list)
    coverage_manifest: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "pr_session_id": self.pr_session_id,
            "created_at": self.created_at,
            "tasks": self.tasks,
            "coverage_manifest": self.coverage_manifest,
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskPlanRecord:
        return cls(
            schema_version=data.get("schema_version", 1),
            run_id=data["run_id"],
            pr_session_id=data["pr_session_id"],
            created_at=data["created_at"],
            tasks=data.get("tasks", []),
            coverage_manifest=data.get("coverage_manifest", {}),
            evidence=data.get("evidence", []),
        )


# ---------------------------------------------------------------------------
# Result record (result.json)
# ---------------------------------------------------------------------------


@dataclass
class ResultRecord:
    """Persisted terminal review result."""

    run_id: str
    pr_session_id: str
    lifecycle: str  # "completed", "failed", "cancelled"
    schema_version: int = RESULT_RECORD_SCHEMA_VERSION
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    findings: list[dict[str, Any]] = field(default_factory=list)
    error_summary: str | None = None
    coverage: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "pr_session_id": self.pr_session_id,
            "lifecycle": self.lifecycle,
            "created_at": self.created_at,
            "findings": self.findings,
            "coverage": self.coverage,
            "usage": self.usage,
        }
        if self.error_summary is not None:
            d["error_summary"] = self.error_summary
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResultRecord:
        return cls(
            schema_version=data.get("schema_version", 1),
            run_id=data["run_id"],
            pr_session_id=data["pr_session_id"],
            lifecycle=data["lifecycle"],
            created_at=data["created_at"],
            findings=data.get("findings", []),
            error_summary=data.get("error_summary"),
            coverage=data.get("coverage", {}),
            usage=data.get("usage", {}),
        )


# ---------------------------------------------------------------------------
# Integrity diagnostics
# ---------------------------------------------------------------------------


@dataclass
class IntegrityFinding:
    """One integrity issue detected during scanning or loading."""

    level: IntegrityLevel
    component: str  # e.g. "run.json", "events.jsonl", "index.json", "agent-sessions.json"
    message: str
    run_id: str | None = None
    file_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "level": self.level.value,
            "component": self.component,
            "message": self.message,
        }
        if self.run_id is not None:
            d["run_id"] = self.run_id
        if self.file_path is not None:
            d["file_path"] = self.file_path
        return d


@dataclass
class IntegrityReport:
    """Aggregated integrity diagnostics for a PR session or run."""

    pr_session_id: str
    checked_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    findings: list[IntegrityFinding] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return all(f.level == IntegrityLevel.OK for f in self.findings)

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.level == IntegrityLevel.ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.level == IntegrityLevel.WARNING)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pr_session_id": self.pr_session_id,
            "checked_at": self.checked_at,
            "is_clean": self.is_clean,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "findings": [f.to_dict() for f in self.findings],
        }


# ---------------------------------------------------------------------------
# Retention policy
# ---------------------------------------------------------------------------


@dataclass
class RetentionPolicy:
    """Configurable retention periods for different artifact types.

    All values are in days.  ``None`` means "keep forever".
    """

    result_days: int | None = 90
    event_days: int | None = 30
    context_days: int | None = 14
    agent_transcript_days: int | None = 7
    empty_session_days: int | None = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_days": self.result_days,
            "event_days": self.event_days,
            "context_days": self.context_days,
            "agent_transcript_days": self.agent_transcript_days,
            "empty_session_days": self.empty_session_days,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RetentionPolicy:
        return cls(
            result_days=data.get("result_days", 90),
            event_days=data.get("event_days", 30),
            context_days=data.get("context_days", 14),
            agent_transcript_days=data.get("agent_transcript_days", 7),
            empty_session_days=data.get("empty_session_days", 1),
        )


# ---------------------------------------------------------------------------
# Unsupported version sentinel
# ---------------------------------------------------------------------------


@dataclass
class UnsupportedVersion:
    """Returned when a persisted record has a newer schema version."""

    component: str
    stored_version: int
    max_supported_version: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "unsupported_version": True,
            "component": self.component,
            "stored_version": self.stored_version,
            "max_supported_version": self.max_supported_version,
        }
