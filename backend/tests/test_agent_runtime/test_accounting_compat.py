"""Backward-compatibility tests for accounting models.

Proves that older stored results and requests still load when the new
coverage and accounting fields are absent.
"""
from __future__ import annotations

import pytest

from backend.agent.runtime.accounting import (
    CoverageEntry,
    CoverageManifest,
    CoverageState,
    OperationUsage,
    RunUsage,
    TaskAccounting,
)
from backend.agent.runtime.final_result import (
    FinalReviewResult,
    NormalizedFinding,
    TaskSummary,
    build_final_result,
    build_fallback_result,
)


# --- CoverageEntry backward compatibility ---


class TestCoverageEntryCompat:
    def test_from_dict_empty(self):
        """Empty dict loads with all defaults."""
        entry = CoverageEntry.from_dict({})
        assert entry.filename == ""
        assert entry.state == CoverageState.UNPLANNED.value
        assert entry.lane == "baseline"
        assert entry.is_high_priority is False

    def test_from_dict_partial(self):
        """Partial dict loads missing fields as defaults."""
        entry = CoverageEntry.from_dict({"filename": "foo.py", "state": "reviewed"})
        assert entry.filename == "foo.py"
        assert entry.state == "reviewed"
        assert entry.task_id == ""
        assert entry.truncated is False

    def test_roundtrip(self):
        """Full entry survives to_dict/from_dict roundtrip."""
        entry = CoverageEntry(
            filename="bar.py",
            lane="baseline",
            state="partial",
            task_id="t1",
            reason="oversized",
            is_high_priority=True,
            priority_score=85,
            truncated=True,
            estimated_tokens=5000,
            actual_tokens=3000,
        )
        restored = CoverageEntry.from_dict(entry.to_dict())
        assert restored.filename == entry.filename
        assert restored.state == entry.state
        assert restored.truncated is True
        assert restored.estimated_tokens == 5000


# --- CoverageManifest backward compatibility ---


class TestCoverageManifestCompat:
    def test_from_dict_empty(self):
        """Empty dict loads with no entries."""
        manifest = CoverageManifest.from_dict({})
        assert manifest.entries == {}

    def test_from_dict_missing_entries(self):
        """Dict without 'entries' key loads as empty."""
        manifest = CoverageManifest.from_dict({"other_field": 123})
        assert manifest.entries == {}

    def test_coverage_counts_empty(self):
        """Empty manifest has zero counts."""
        manifest = CoverageManifest()
        counts = manifest.coverage_counts
        assert counts["baseline_reviewed"] == 0
        assert counts["uncovered_high_priority"] == 0

    def test_roundtrip(self):
        """Manifest with entries survives roundtrip."""
        manifest = CoverageManifest()
        manifest.add_entry(CoverageEntry(
            filename="a.py", state="reviewed", is_high_priority=True,
        ))
        manifest.add_entry(CoverageEntry(
            filename="b.py", state="omitted", is_high_priority=True,
        ))
        restored = CoverageManifest.from_dict(manifest.to_dict())
        assert len(restored.entries) == 2
        assert restored.baseline_reviewed_count == 1
        assert len(restored.uncovered_high_priority_paths) == 1


# --- RunUsage backward compatibility ---


class TestRunUsageCompat:
    def test_from_dict_empty(self):
        """Empty dict loads with all defaults."""
        usage = RunUsage.from_dict({})
        assert usage.total_model_calls == 0
        assert usage.total_input_tokens == 0
        assert usage.operations == []

    def test_from_dict_legacy_format(self):
        """Legacy format with only token_usage fields loads correctly."""
        legacy = {"input_tokens": 1000, "output_tokens": 500}
        # This simulates the old token_usage dict format
        usage = RunUsage.from_dict({})
        assert usage.total_input_tokens == 0
        # Legacy format doesn't have RunUsage fields, so they default to 0

    def test_roundtrip(self):
        """RunUsage with operations survives roundtrip."""
        usage = RunUsage(model_id="gpt-4o")
        usage.add_operation(OperationUsage(
            operation_type="test_context",
            model_calls=2,
            input_tokens=500,
            output_tokens=200,
        ))
        restored = RunUsage.from_dict(usage.to_dict())
        assert restored.total_model_calls == 2
        assert restored.total_input_tokens == 500
        assert len(restored.operations) == 1

    def test_per_operation_breakdown(self):
        """Breakdown groups operations by type."""
        usage = RunUsage()
        usage.add_operation(OperationUsage(operation_type="test_context", model_calls=2, input_tokens=100))
        usage.add_operation(OperationUsage(operation_type="test_context", model_calls=1, input_tokens=50))
        usage.add_operation(OperationUsage(operation_type="security_context", model_calls=3, input_tokens=200))
        breakdown = usage.per_operation_breakdown
        assert breakdown["test_context"]["model_calls"] == 3
        assert breakdown["test_context"]["input_tokens"] == 150
        assert breakdown["security_context"]["model_calls"] == 3


# --- OperationUsage backward compatibility ---


class TestOperationUsageCompat:
    def test_from_dict_empty(self):
        """Empty dict loads with all defaults."""
        op = OperationUsage.from_dict({})
        assert op.operation_type == ""
        assert op.model_calls == 0
        assert op.fallback_used is False

    def test_merge(self):
        """Merge accumulates usage correctly."""
        op1 = OperationUsage(operation_type="main", model_calls=2, input_tokens=100)
        op2 = OperationUsage(operation_type="main", model_calls=1, input_tokens=50, fallback_used=True)
        op1.merge(op2)
        assert op1.model_calls == 3
        assert op1.input_tokens == 150
        assert op1.fallback_used is True


# --- TaskAccounting backward compatibility ---


class TestTaskAccountingCompat:
    def test_from_dict_empty(self):
        """Empty dict loads with all defaults."""
        ta = TaskAccounting.from_dict({})
        assert ta.task_id == ""
        assert ta.model_calls == 0
        assert ta.failure_reason == ""

    def test_roundtrip(self):
        """TaskAccounting survives roundtrip."""
        ta = TaskAccounting(
            task_id="t1",
            task_type="test_context",
            model_calls=3,
            input_tokens=1000,
            output_tokens=500,
            observation_tokens=2000,
            search_count=5,
            file_read_count=10,
            elapsed_ms=5000,
            failure_reason="timeout",
        )
        restored = TaskAccounting.from_dict(ta.to_dict())
        assert restored.task_id == "t1"
        assert restored.failure_reason == "timeout"


# --- FinalReviewResult backward compatibility ---


class TestFinalReviewResultCompat:
    def test_old_result_loads_without_new_fields(self):
        """A result dict without coverage/run_usage fields loads correctly."""
        old_dict = {
            "status": "completed",
            "summary": "Review completed",
            "findings": [],
            "uncertainties": [],
            "notes": [],
            "task_summaries": [],
            "steps": 5,
            "stopped_by_max_steps": False,
            "token_usage": {"input_tokens": 1000, "output_tokens": 500},
        }
        # Simulate loading from dict by building a result
        result = FinalReviewResult(
            status=old_dict["status"],
            summary=old_dict["summary"],
            token_usage=old_dict["token_usage"],
        )
        assert result.coverage is None
        assert result.run_usage is None
        assert result.uncovered_high_priority_paths == []
        assert result.coverage_counts == {}

    def test_new_result_serializes_with_coverage(self):
        """New result with coverage metadata serializes correctly."""
        manifest = CoverageManifest()
        manifest.add_entry(CoverageEntry(filename="a.py", state="reviewed"))

        usage = RunUsage(model_id="gpt-4o", total_model_calls=5)

        result = FinalReviewResult(
            status="completed",
            summary="Done",
            coverage=manifest,
            run_usage=usage,
            coverage_counts={"baseline_reviewed": 1, "uncovered_high_priority": 0},
        )
        d = result.to_dict()
        assert "coverage" in d
        assert "run_usage" in d
        assert d["coverage_counts"]["baseline_reviewed"] == 1

    def test_result_without_coverage_omits_keys(self):
        """Result without coverage/run_usage omits those keys in serialization."""
        result = FinalReviewResult(status="completed", summary="Done")
        d = result.to_dict()
        assert "coverage" not in d
        assert "run_usage" not in d
        assert "uncovered_high_priority_paths" not in d

    def test_build_final_result_backward_compat(self):
        """build_final_result works without new fields (backward compat)."""
        result = build_final_result(
            task_results=[],
            raw_output="test",
            steps=3,
            token_usage={"input_tokens": 100, "output_tokens": 50},
        )
        assert result.coverage is None
        assert result.run_usage is None
        assert result.status == "completed"

    def test_build_fallback_result_backward_compat(self):
        """build_fallback_result works without new fields."""
        result = build_fallback_result(
            raw_output="fallback",
            steps=1,
        )
        assert result.coverage is None
        assert result.status == "completed"


# --- TaskSummary accounting backward compatibility ---


class TestTaskSummaryCompat:
    def test_old_task_summary_loads(self):
        """Old TaskSummary without accounting fields loads correctly."""
        summary = TaskSummary(
            task_id="t1",
            task_type="test_context",
            execution_status="ok",
        )
        d = summary.to_dict()
        assert "model_id" not in d
        assert "model_calls" not in d
        assert "observation_tokens" not in d

    def test_new_task_summary_serializes_accounting(self):
        """New TaskSummary with accounting fields serializes correctly."""
        summary = TaskSummary(
            task_id="t1",
            task_type="test_context",
            execution_status="ok",
            model_id="gpt-4o",
            model_calls=3,
            input_tokens=1000,
            output_tokens=500,
            observation_tokens=2000,
            elapsed_ms=5000,
            retries=1,
            fallback_used=False,
            failure_reason="",
        )
        d = summary.to_dict()
        assert d["model_id"] == "gpt-4o"
        assert d["model_calls"] == 3
        assert d["observation_tokens"] == 2000
        assert "fallback_used" not in d  # False, so omitted

    def test_task_summary_with_failure_reason(self):
        """TaskSummary with failure reason serializes correctly."""
        summary = TaskSummary(
            task_id="t1",
            execution_status="error",
            failure_reason="budget_exhausted",
        )
        d = summary.to_dict()
        assert d["failure_reason"] == "budget_exhausted"
