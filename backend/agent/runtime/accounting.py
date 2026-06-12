"""Resource accounting models for hybrid review quality guardrails.

Provides compatibility-safe models for coverage tracking, aggregate run usage,
per-operation usage, and structured runtime failure reasons. All new fields have
defaults so older stored results and requests still load correctly.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


# --- Enums ---


class CoverageState(str, enum.Enum):
    """Coverage state for a single file in the review plan."""
    PLANNED = "planned"        # Assigned to a baseline task
    REVIEWED = "reviewed"      # Successfully reviewed
    PARTIAL = "partial"        # Partially reviewed (truncated or oversize)
    OMITTED = "omitted"        # Omitted by plan budget or prioritization
    CANCELLED = "cancelled"    # Cancelled before completion
    TIMEOUT = "timeout"        # Timed out during execution
    FAILED = "failed"          # Failed during execution
    UNPLANNED = "unplanned"    # Not selected for baseline review


class RuntimeFailureReason(str, enum.Enum):
    """Structured reasons for runtime failures."""
    TIMEOUT = "timeout"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CANCELLED = "cancelled"
    MAX_STEPS = "max_steps"
    CONTEXT_LENGTH = "context_length"
    MODEL_ERROR = "model_error"
    TOOL_ERROR = "tool_error"
    VALIDATION_ERROR = "validation_error"
    UNKNOWN = "unknown"


class CoverageLane(str, enum.Enum):
    """Lane type for coverage tracking."""
    BASELINE = "baseline"
    SPECIALIST = "specialist"


# --- Coverage Models ---


@dataclass
class CoverageEntry:
    """Planned/runtime coverage entry for a single changed file.

    Tracks the file through the review pipeline from planning to completion.
    All fields have defaults for backward compatibility.
    """
    filename: str = ""
    lane: str = CoverageLane.BASELINE.value
    state: str = CoverageState.UNPLANNED.value
    task_id: str = ""
    reason: str = ""                    # Why omitted/failed/partial
    is_high_priority: bool = False
    priority_score: int = 0
    truncated: bool = False             # True if patch was too large
    estimated_tokens: int = 0           # Estimated patch tokens
    actual_tokens: int = 0              # Actual observation tokens charged

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "lane": self.lane,
            "state": self.state,
            "task_id": self.task_id,
            "reason": self.reason,
            "is_high_priority": self.is_high_priority,
            "priority_score": self.priority_score,
            "truncated": self.truncated,
            "estimated_tokens": self.estimated_tokens,
            "actual_tokens": self.actual_tokens,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoverageEntry:
        return cls(
            filename=data.get("filename", ""),
            lane=data.get("lane", CoverageLane.BASELINE.value),
            state=data.get("state", CoverageState.UNPLANNED.value),
            task_id=data.get("task_id", ""),
            reason=data.get("reason", ""),
            is_high_priority=data.get("is_high_priority", False),
            priority_score=data.get("priority_score", 0),
            truncated=data.get("truncated", False),
            estimated_tokens=data.get("estimated_tokens", 0),
            actual_tokens=data.get("actual_tokens", 0),
        )


@dataclass
class CoverageManifest:
    """Deterministic coverage manifest keyed by changed file.

    Records baseline and specialist coverage separately.
    """
    entries: dict[str, CoverageEntry] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"entries": {k: v.to_dict() for k, v in self.entries.items()}}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoverageManifest:
        entries = {}
        for k, v in data.get("entries", {}).items():
            entries[k] = CoverageEntry.from_dict(v)
        return cls(entries=entries)

    def add_entry(self, entry: CoverageEntry) -> None:
        key = f"{entry.filename}:{entry.lane}"
        self.entries[key] = entry

    def get_entry(self, filename: str, lane: str = CoverageLane.BASELINE.value) -> CoverageEntry | None:
        return self.entries.get(f"{filename}:{lane}")

    def update_state(
        self,
        filename: str,
        lane: str,
        state: str,
        *,
        task_id: str = "",
        reason: str = "",
        truncated: bool = False,
        actual_tokens: int = 0,
    ) -> None:
        key = f"{filename}:{lane}"
        entry = self.entries.get(key)
        if entry:
            entry.state = state
            if task_id:
                entry.task_id = task_id
            if reason:
                entry.reason = reason
            if truncated:
                entry.truncated = truncated
            if actual_tokens:
                entry.actual_tokens = actual_tokens

    @property
    def baseline_reviewed_count(self) -> int:
        return sum(
            1 for e in self.entries.values()
            if e.lane == CoverageLane.BASELINE.value and e.state == CoverageState.REVIEWED.value
        )

    @property
    def baseline_partial_count(self) -> int:
        return sum(
            1 for e in self.entries.values()
            if e.lane == CoverageLane.BASELINE.value and e.state == CoverageState.PARTIAL.value
        )

    @property
    def baseline_omitted_count(self) -> int:
        return sum(
            1 for e in self.entries.values()
            if e.lane == CoverageLane.BASELINE.value and e.state == CoverageState.OMITTED.value
        )

    @property
    def baseline_failed_count(self) -> int:
        return sum(
            1 for e in self.entries.values()
            if e.lane == CoverageLane.BASELINE.value
            and e.state in (CoverageState.FAILED.value, CoverageState.TIMEOUT.value)
        )

    @property
    def uncovered_high_priority_paths(self) -> list[str]:
        """High-priority files without successful baseline coverage."""
        return [
            e.filename for e in self.entries.values()
            if e.lane == CoverageLane.BASELINE.value
            and e.is_high_priority
            and e.state not in (CoverageState.REVIEWED.value, CoverageState.PARTIAL.value)
        ]

    @property
    def coverage_counts(self) -> dict[str, int]:
        return {
            "baseline_reviewed": self.baseline_reviewed_count,
            "baseline_partial": self.baseline_partial_count,
            "baseline_omitted": self.baseline_omitted_count,
            "baseline_failed": self.baseline_failed_count,
            "uncovered_high_priority": len(self.uncovered_high_priority_paths),
        }


# --- Usage Accounting Models ---


@dataclass
class OperationUsage:
    """Resource usage for a single operation type.

    Tracks model calls, tokens, elapsed time, retries, and model identity
    for one category of work (e.g., a subagent, compaction, repair).
    """
    operation_type: str = ""            # main_synthesis, test_context, compaction, repair, etc.
    model_id: str = ""                  # Model used for this operation
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    observation_tokens: int = 0         # Estimated tool observation tokens
    elapsed_ms: int = 0
    retries: int = 0
    fallback_used: bool = False
    task_id: str = ""                   # Associated task ID if applicable
    agent_type: str = ""                # Agent type if applicable

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_type": self.operation_type,
            "model_id": self.model_id,
            "model_calls": self.model_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "observation_tokens": self.observation_tokens,
            "elapsed_ms": self.elapsed_ms,
            "retries": self.retries,
            "fallback_used": self.fallback_used,
            "task_id": self.task_id,
            "agent_type": self.agent_type,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OperationUsage:
        return cls(
            operation_type=data.get("operation_type", ""),
            model_id=data.get("model_id", ""),
            model_calls=data.get("model_calls", 0),
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            observation_tokens=data.get("observation_tokens", 0),
            elapsed_ms=data.get("elapsed_ms", 0),
            retries=data.get("retries", 0),
            fallback_used=data.get("fallback_used", False),
            task_id=data.get("task_id", ""),
            agent_type=data.get("agent_type", ""),
        )

    def merge(self, other: OperationUsage) -> None:
        """Merge another operation's usage into this one."""
        self.model_calls += other.model_calls
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.observation_tokens += other.observation_tokens
        self.elapsed_ms += other.elapsed_ms
        self.retries += other.retries
        if other.fallback_used:
            self.fallback_used = True


@dataclass
class RunUsage:
    """Aggregate run-level usage accounting.

    Immutable record of all resource consumption across the entire review run.
    All fields have defaults for backward compatibility.
    """
    model_id: str = ""
    total_model_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_observation_tokens: int = 0
    total_elapsed_ms: int = 0
    total_retries: int = 0
    total_fallbacks: int = 0
    task_count: int = 0
    operations: list[OperationUsage] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "total_model_calls": self.total_model_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_observation_tokens": self.total_observation_tokens,
            "total_elapsed_ms": self.total_elapsed_ms,
            "total_retries": self.total_retries,
            "total_fallbacks": self.total_fallbacks,
            "task_count": self.task_count,
            "operations": [op.to_dict() for op in self.operations],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunUsage:
        operations = [OperationUsage.from_dict(op) for op in data.get("operations", [])]
        return cls(
            model_id=data.get("model_id", ""),
            total_model_calls=data.get("total_model_calls", 0),
            total_input_tokens=data.get("total_input_tokens", 0),
            total_output_tokens=data.get("total_output_tokens", 0),
            total_observation_tokens=data.get("total_observation_tokens", 0),
            total_elapsed_ms=data.get("total_elapsed_ms", 0),
            total_retries=data.get("total_retries", 0),
            total_fallbacks=data.get("total_fallbacks", 0),
            task_count=data.get("task_count", 0),
            operations=operations,
        )

    def add_operation(self, op: OperationUsage) -> None:
        """Add an operation's usage to the aggregate totals."""
        self.operations.append(op)
        self.total_model_calls += op.model_calls
        self.total_input_tokens += op.input_tokens
        self.total_output_tokens += op.output_tokens
        self.total_observation_tokens += op.observation_tokens
        self.total_elapsed_ms += op.elapsed_ms
        self.total_retries += op.retries
        if op.fallback_used:
            self.total_fallbacks += 1

    @property
    def per_operation_breakdown(self) -> dict[str, dict[str, int]]:
        """Group usage by operation type for result serialization."""
        breakdown: dict[str, dict[str, int]] = {}
        for op in self.operations:
            key = op.operation_type or "unknown"
            if key not in breakdown:
                breakdown[key] = {
                    "model_calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "observation_tokens": 0,
                    "elapsed_ms": 0,
                    "retries": 0,
                }
            bucket = breakdown[key]
            bucket["model_calls"] += op.model_calls
            bucket["input_tokens"] += op.input_tokens
            bucket["output_tokens"] += op.output_tokens
            bucket["observation_tokens"] += op.observation_tokens
            bucket["elapsed_ms"] += op.elapsed_ms
            bucket["retries"] += op.retries
        return breakdown


@dataclass
class TaskAccounting:
    """Per-task resource accounting with budget enforcement."""
    task_id: str = ""
    task_type: str = ""
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    observation_tokens: int = 0
    search_count: int = 0
    file_read_count: int = 0
    elapsed_ms: int = 0
    failure_reason: str = ""
    failure_detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "model_calls": self.model_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "observation_tokens": self.observation_tokens,
            "search_count": self.search_count,
            "file_read_count": self.file_read_count,
            "elapsed_ms": self.elapsed_ms,
            "failure_reason": self.failure_reason,
            "failure_detail": self.failure_detail,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskAccounting:
        return cls(
            task_id=data.get("task_id", ""),
            task_type=data.get("task_type", ""),
            model_calls=data.get("model_calls", 0),
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            observation_tokens=data.get("observation_tokens", 0),
            search_count=data.get("search_count", 0),
            file_read_count=data.get("file_read_count", 0),
            elapsed_ms=data.get("elapsed_ms", 0),
            failure_reason=data.get("failure_reason", ""),
            failure_detail=data.get("failure_detail", ""),
        )
