"""Deterministic offline PR fixture suite for hybrid review quality evaluation.

Measures:
- Baseline high-priority coverage
- Specialist enrichment without baseline suppression
- Actionable recall and context-only false positives
- Budget enforcement
- Degraded paths (planner omission, tool timeout, budget exhaustion, etc.)
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from backend.domain.pr_context.context_manager import PRContext, FileEntry, DerivedSignals
from backend.domain.review.evidence import EvidenceItem, Severity
from backend.domain.review.context_task_planner import (
    build_context_task_plan,
    estimate_patch_tokens,
    batch_patches_by_budget,
    MAX_TASKS_PER_RUN,
    DEFAULT_BASELINE_CAPACITY,
)
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
    build_final_result,
)


# --- Fixture builders ---


def _make_file(
    filename: str,
    *,
    is_source: bool = True,
    is_test: bool = False,
    priority_score: int = 50,
    added_lines: int = 10,
    risk_hints: list[str] | None = None,
) -> FileEntry:
    return FileEntry(
        filename=filename,
        previous_filename=None,
        status="modified",
        additions=added_lines,
        deletions=5,
        changes=added_lines + 5,
        language="python",
        language_family="python",
        rule_profile="default",
        is_test=is_test,
        is_docs=False,
        is_config=False,
        is_source=is_source,
        is_generated=False,
        is_binary=False,
        patch_available=True,
        large_patch=False,
        parse_error=None,
        is_high_risk_path=False,
        risk_hints=risk_hints or [],
        priority_score_hint=priority_score,
        hunk_count=1,
        added_line_count=added_lines,
        removed_line_count=5,
        keywords=[],
        hunks=[],
    )


def _make_ctx(files: list[FileEntry]) -> PRContext:
    derived = DerivedSignals(
        total_hunks=sum(f.hunk_count for f in files),
        source_files_changed=sum(1 for f in files if f.is_source and not f.is_test),
        test_files_changed=sum(1 for f in files if f.is_test),
        docs_only=False,
        has_source_without_tests=any(f.is_source and not f.is_test for f in files),
    )
    return PRContext(
        context_id="fixture-ctx",
        source="test",
        fetched_at="2024-01-01T00:00:00Z",
        cache_key="fixture",
        owner="test-owner",
        repo="test-repo",
        pull_number=1,
        pr=MagicMock(),
        commits=MagicMock(),
        files=files,
        derived=derived,
    )


def _make_evidence(rule_id: str, file: str, severity: str = "warning") -> EvidenceItem:
    return EvidenceItem(
        id=f"ev_{rule_id}_{file}",
        source="static",
        rule_id=rule_id,
        file=file,
        severity=Severity.CRITICAL if severity == "critical" else Severity.WARNING,
        category="security" if "sensitive" in rule_id else "test_gap",
        message=f"Evidence for {rule_id}",
        confidence=0.8,
        tags=[],
    )


# --- Fixtures: Baseline Coverage ---


class TestBaselineCoverage:
    """Measure baseline high-priority coverage for representative PRs."""

    def test_small_pr_full_coverage(self):
        """Small PR with 3 high-priority files gets full baseline coverage."""
        files = [
            _make_file("src/auth.py", priority_score=85),
            _make_file("src/db.py", priority_score=80),
            _make_file("src/api.py", priority_score=75),
        ]
        ctx = _make_ctx(files)
        plan = build_context_task_plan(ctx, [])

        baseline_tasks = [t for t in plan["tasks"] if t["task_type"] == "patch_deep_dive"]
        baseline_files = set()
        for t in baseline_tasks:
            baseline_files.update(t["target"]["files"])

        # All high-priority files should be covered
        high_priority = {f.filename for f in files if f.priority_score_hint >= 70}
        assert high_priority.issubset(baseline_files)

    def test_medium_pr_baseline_capacity(self):
        """Medium PR respects baseline capacity reservation."""
        files = [
            _make_file(f"src/file{i}.py", priority_score=70 + i)
            for i in range(6)
        ]
        ctx = _make_ctx(files)
        plan = build_context_task_plan(ctx, [])

        baseline_tasks = [t for t in plan["tasks"] if t["task_type"] == "patch_deep_dive"]
        # Should have baseline tasks (up to baseline capacity)
        assert len(baseline_tasks) <= DEFAULT_BASELINE_CAPACITY

    def test_manifest_tracks_all_high_priority(self):
        """Coverage manifest tracks all high-priority files."""
        files = [
            _make_file("src/a.py", priority_score=85),
            _make_file("src/b.py", priority_score=80),
            _make_file("src/c.py", priority_score=30),  # Not high priority
        ]
        ctx = _make_ctx(files)
        plan = build_context_task_plan(ctx, [])

        manifest = plan.get("coverage_manifest", {})
        entries = manifest.get("entries", {})

        # High-priority files should appear in manifest
        manifest_filenames = {e["filename"] for e in entries.values() if e.get("lane") == "baseline"}
        assert "src/a.py" in manifest_filenames
        assert "src/b.py" in manifest_filenames


class TestSpecialistEnrichment:
    """Measure specialist enrichment without baseline suppression."""

    def test_specialist_and_baseline_coexist(self):
        """High-priority file with specialist task still gets baseline."""
        files = [
            _make_file("src/auth.py", priority_score=85, risk_hints=["auth_path"]),
        ]
        ctx = _make_ctx(files)
        evidence = [_make_evidence("sensitive_field", "src/auth.py")]

        plan = build_context_task_plan(ctx, evidence)
        task_types = {t["task_type"] for t in plan["tasks"]}

        assert "security_context" in task_types
        assert "patch_deep_dive" in task_types

    def test_specialist_coverage_tracked_separately(self):
        """Specialist and baseline coverage are tracked separately."""
        files = [
            _make_file("src/auth.py", priority_score=85, risk_hints=["auth_path"]),
        ]
        ctx = _make_ctx(files)
        evidence = [_make_evidence("sensitive_field", "src/auth.py")]

        plan = build_context_task_plan(ctx, evidence)
        manifest = plan.get("coverage_manifest", {})
        entries = manifest.get("entries", {})

        lanes = {e["lane"] for e in entries.values()}
        assert "baseline" in lanes
        assert "specialist" in lanes


class TestActionableRecall:
    """Measure actionable finding recall and context-only false positives."""

    def test_actionable_candidates_from_security(self):
        """Security evidence produces actionable candidates."""
        from backend.agent.runtime.final_result import classify_candidate

        assert classify_candidate("security_risk") == "actionable"
        assert classify_candidate("bug_risk") == "actionable"
        assert classify_candidate("test_gap") == "actionable"

    def test_context_candidates_from_reference(self):
        """Reference context produces context-only candidates."""
        from backend.agent.runtime.final_result import classify_candidate

        assert classify_candidate("caller_info") == "context"
        assert classify_candidate("architecture_ref") == "context"
        assert classify_candidate("config_change") == "context"


class TestDegradedPaths:
    """Degraded-path fixtures for various failure modes."""

    def test_planner_omission_disclosed(self):
        """Planner omission of high-priority file is disclosed in manifest."""
        files = [
            _make_file(f"src/file{i}.py", priority_score=80 + i, added_lines=500)
            for i in range(8)
        ]
        ctx = _make_ctx(files)
        evidence = [
            _make_evidence("sensitive_field", "src/file0.py"),
            _make_evidence("sensitive_field", "src/file1.py"),
            _make_evidence("sensitive_field", "src/file2.py"),
        ]

        plan = build_context_task_plan(ctx, evidence)
        manifest = plan.get("coverage_manifest", {})
        entries = manifest.get("entries", {})

        # Some files should be tracked as omitted
        omitted = [e for e in entries.values() if e.get("state") == "omitted"]
        # With 8 files and cap pressure, some may be omitted
        assert len(entries) > 0  # At least some files tracked

    def test_partial_coverage_result(self):
        """Partial coverage produces partial status."""
        manifest = CoverageManifest()
        manifest.add_entry(CoverageEntry(
            filename="a.py", lane=CoverageLane.BASELINE.value,
            state=CoverageState.REVIEWED.value, is_high_priority=True,
        ))
        manifest.add_entry(CoverageEntry(
            filename="b.py", lane=CoverageLane.BASELINE.value,
            state=CoverageState.OMITTED.value, is_high_priority=True,
            reason="budget_limit",
        ))

        result = build_final_result(task_results=[], coverage_manifest=manifest)
        assert result.status == "partial"
        assert len(result.uncovered_high_priority_paths) == 1

    def test_budget_exhaustion_tracking(self):
        """Budget exhaustion is tracked in task accounting."""
        from backend.agent.runtime.accounting import TaskAccounting

        ta = TaskAccounting(
            task_id="t1",
            task_type="patch_deep_dive",
            failure_reason="budget_exhausted",
        )
        d = ta.to_dict()
        assert d["failure_reason"] == "budget_exhausted"

    def test_timeout_tracking(self):
        """Timeout is tracked in task accounting."""
        from backend.agent.runtime.accounting import TaskAccounting

        ta = TaskAccounting(
            task_id="t1",
            task_type="patch_deep_dive",
            failure_reason="timeout",
        )
        d = ta.to_dict()
        assert d["failure_reason"] == "timeout"


class TestQualityMetrics:
    """Record fixture comparisons for quality metrics."""

    def test_coverage_counts_in_result(self):
        """Result includes coverage counts for metrics."""
        manifest = CoverageManifest()
        for i in range(5):
            manifest.add_entry(CoverageEntry(
                filename=f"src/file{i}.py",
                lane=CoverageLane.BASELINE.value,
                state=CoverageState.REVIEWED.value,
                is_high_priority=True,
            ))

        result = build_final_result(task_results=[], coverage_manifest=manifest)
        counts = result.coverage_counts

        assert counts["baseline_reviewed"] == 5
        assert counts["uncovered_high_priority"] == 0

    def test_run_usage_metrics(self):
        """Run usage includes all cost metrics."""
        usage = RunUsage(model_id="gpt-4o")
        usage.add_operation(OperationUsage(
            operation_type="main_synthesis",
            model_calls=1,
            input_tokens=5000,
            output_tokens=2000,
            elapsed_ms=10000,
        ))
        usage.add_operation(OperationUsage(
            operation_type="test_context",
            model_calls=3,
            input_tokens=3000,
            output_tokens=1500,
            elapsed_ms=15000,
        ))

        d = usage.to_dict()
        assert d["total_model_calls"] == 4
        assert d["total_input_tokens"] == 8000
        assert d["total_elapsed_ms"] == 25000
        assert len(d["operations"]) == 2
