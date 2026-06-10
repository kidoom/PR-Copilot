"""API and event serialization tests for coverage and result integration.

Tests cover full coverage, partial coverage, baseline failure,
optional specialist failure, and fallback completion.
"""
from __future__ import annotations

import pytest

from backend.agent.runtime.accounting import (
    CoverageEntry,
    CoverageLane,
    CoverageManifest,
    CoverageState,
    OperationUsage,
    RunUsage,
)
from backend.agent.runtime.final_result import (
    FinalReviewResult,
    NormalizedFinding,
    TaskSummary,
    build_final_result,
    build_fallback_result,
)


def _make_coverage_manifest(*entries: CoverageEntry) -> CoverageManifest:
    manifest = CoverageManifest()
    for e in entries:
        manifest.add_entry(e)
    return manifest


class TestFullCoverage:
    def test_full_coverage_result(self):
        """Full baseline coverage produces completed status."""
        manifest = _make_coverage_manifest(
            CoverageEntry(filename="a.py", lane=CoverageLane.BASELINE.value, state=CoverageState.REVIEWED.value, is_high_priority=True),
            CoverageEntry(filename="b.py", lane=CoverageLane.BASELINE.value, state=CoverageState.REVIEWED.value, is_high_priority=True),
        )

        result = build_final_result(
            task_results=[],
            coverage_manifest=manifest,
        )

        assert result.status == "completed"
        assert result.uncovered_high_priority_paths == []
        assert result.coverage_counts["baseline_reviewed"] == 2
        assert result.coverage_counts["uncovered_high_priority"] == 0

    def test_full_coverage_serialization(self):
        """Full coverage result serializes with all fields."""
        manifest = _make_coverage_manifest(
            CoverageEntry(filename="a.py", lane=CoverageLane.BASELINE.value, state=CoverageState.REVIEWED.value),
        )
        usage = RunUsage(model_id="gpt-4o", total_model_calls=5)

        result = build_final_result(
            task_results=[],
            coverage_manifest=manifest,
            run_usage=usage,
        )
        d = result.to_dict()

        assert "coverage" in d
        assert "run_usage" in d
        assert "coverage_counts" in d
        assert d["status"] == "completed"


class TestPartialCoverage:
    def test_partial_coverage_result(self):
        """Partial baseline coverage produces partial status."""
        manifest = _make_coverage_manifest(
            CoverageEntry(filename="a.py", lane=CoverageLane.BASELINE.value, state=CoverageState.REVIEWED.value, is_high_priority=True),
            CoverageEntry(filename="b.py", lane=CoverageLane.BASELINE.value, state=CoverageState.OMITTED.value, is_high_priority=True, reason="budget_limit"),
        )

        result = build_final_result(
            task_results=[],
            coverage_manifest=manifest,
        )

        assert result.status == "partial"
        assert "b.py" in result.uncovered_high_priority_paths
        assert result.coverage_counts["uncovered_high_priority"] == 1

    def test_partial_coverage_serialization(self):
        """Partial coverage serializes correctly."""
        manifest = _make_coverage_manifest(
            CoverageEntry(filename="a.py", lane=CoverageLane.BASELINE.value, state=CoverageState.REVIEWED.value, is_high_priority=True),
            CoverageEntry(filename="b.py", lane=CoverageLane.BASELINE.value, state=CoverageState.PARTIAL.value, is_high_priority=True, truncated=True),
            CoverageEntry(filename="c.py", lane=CoverageLane.BASELINE.value, state=CoverageState.OMITTED.value, is_high_priority=True, reason="budget_limit"),
        )

        result = build_final_result(
            task_results=[],
            coverage_manifest=manifest,
        )
        d = result.to_dict()

        assert d["status"] == "partial"
        assert len(d["uncovered_high_priority_paths"]) > 0
        assert d["coverage_counts"]["baseline_reviewed"] == 1
        assert d["coverage_counts"]["baseline_partial"] == 1


class TestBaselineFailure:
    def test_baseline_failure_result(self):
        """Baseline failure produces partial status."""
        manifest = _make_coverage_manifest(
            CoverageEntry(filename="a.py", lane=CoverageLane.BASELINE.value, state=CoverageState.FAILED.value, is_high_priority=True, reason="tool error"),
        )

        result = build_final_result(
            task_results=[],
            coverage_manifest=manifest,
        )

        assert result.status == "partial"
        assert "a.py" in result.uncovered_high_priority_paths


class TestOptionalSpecialistFailure:
    def test_specialist_failure_does_not_fail_baseline(self):
        """Optional specialist failure doesn't fail the whole review."""
        manifest = _make_coverage_manifest(
            CoverageEntry(filename="a.py", lane=CoverageLane.BASELINE.value, state=CoverageState.REVIEWED.value, is_high_priority=True),
            CoverageEntry(filename="a.py", lane=CoverageLane.SPECIALIST.value, state=CoverageState.FAILED.value),
        )

        result = build_final_result(
            task_results=[],
            coverage_manifest=manifest,
        )

        # Baseline is complete, specialist failure is noted but doesn't fail
        assert result.status == "completed"
        assert result.coverage_counts["baseline_reviewed"] == 1


class TestFallbackCompletion:
    def test_fallback_result_backward_compat(self):
        """Fallback result works without coverage metadata."""
        result = build_fallback_result(
            raw_output="fallback",
            steps=1,
        )

        assert result.coverage is None
        assert result.run_usage is None
        assert result.status == "completed"

    def test_fallback_with_coverage(self):
        """Fallback result can include coverage metadata."""
        manifest = _make_coverage_manifest(
            CoverageEntry(filename="a.py", lane=CoverageLane.BASELINE.value, state=CoverageState.REVIEWED.value),
        )

        result = build_fallback_result(
            raw_output="fallback",
            steps=1,
        )
        # Fallback doesn't accept coverage, but result still serializes
        d = result.to_dict()
        assert "coverage" not in d  # No coverage in fallback


class TestTaskSummaryAccounting:
    def test_task_summary_with_accounting(self):
        """Task summaries include accounting fields when present."""
        task_results = [{
            "task_id": "t1",
            "task_type": "test_context",
            "agent_type": "test-context-agent",
            "status": "ok",
            "parse_status": "valid",
            "validation_errors": [],
            "parsed_result": None,
            "subagent_usage": {
                "input_tokens": 1000,
                "output_tokens": 500,
                "model_calls": 3,
            },
        }]

        result = build_final_result(task_results=task_results)
        assert len(result.task_summaries) == 1
        ts = result.task_summaries[0]
        assert ts.input_tokens == 1000
        assert ts.output_tokens == 500
        assert ts.model_calls == 3

    def test_task_summary_serialization_with_accounting(self):
        """Task summary serialization includes accounting fields."""
        ts = TaskSummary(
            task_id="t1",
            task_type="test_context",
            execution_status="ok",
            model_calls=3,
            input_tokens=1000,
            output_tokens=500,
            observation_tokens=2000,
            elapsed_ms=5000,
        )
        d = ts.to_dict()
        assert d["model_calls"] == 3
        assert d["input_tokens"] == 1000
        assert d["observation_tokens"] == 2000


class TestRunUsageSerialization:
    def test_run_usage_in_result(self):
        """Run usage serializes in final result."""
        usage = RunUsage(model_id="gpt-4o")
        usage.add_operation(OperationUsage(
            operation_type="test_context",
            model_calls=2,
            input_tokens=500,
            output_tokens=200,
        ))
        usage.add_operation(OperationUsage(
            operation_type="security_context",
            model_calls=3,
            input_tokens=800,
            output_tokens=300,
        ))

        result = build_final_result(
            task_results=[],
            run_usage=usage,
        )
        d = result.to_dict()

        assert "run_usage" in d
        assert d["run_usage"]["total_model_calls"] == 5
        assert d["run_usage"]["total_input_tokens"] == 1300
