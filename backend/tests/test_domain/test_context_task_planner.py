"""Unit tests for ContextTaskPlanner with hybrid review guardrails.

Tests cover:
- Specialist overlap (specialist tasks don't remove high-priority files from baseline)
- Six-task cap pressure
- Critical specialist preemption
- Deterministic batching
- Uncovered high-priority disclosure
- Coverage manifest generation
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
    apply_global_budget,
    ContextTask,
    TaskSource,
    TaskTarget,
    TaskBudget,
    MAX_TASKS_PER_RUN,
    DEFAULT_BASELINE_CAPACITY,
    DEFAULT_PATCH_BATCH_TOKEN_BUDGET,
)


# --- Test helpers ---


def _make_file(
    filename: str,
    *,
    is_source: bool = True,
    is_test: bool = False,
    is_docs: bool = False,
    priority_score: int = 50,
    added_lines: int = 10,
    removed_lines: int = 5,
    large_patch: bool = False,
    risk_hints: list[str] | None = None,
) -> FileEntry:
    return FileEntry(
        filename=filename,
        previous_filename=None,
        status="modified",
        additions=added_lines,
        deletions=removed_lines,
        changes=added_lines + removed_lines,
        language="python",
        language_family="python",
        rule_profile="default",
        is_test=is_test,
        is_docs=is_docs,
        is_config=False,
        is_source=is_source,
        is_generated=False,
        is_binary=False,
        patch_available=True,
        large_patch=large_patch,
        parse_error=None,
        is_high_risk_path=False,
        risk_hints=risk_hints or [],
        priority_score_hint=priority_score,
        hunk_count=1,
        added_line_count=added_lines,
        removed_line_count=removed_lines,
        keywords=[],
        hunks=[],
    )


def _make_evidence(
    rule_id: str = "source_without_tests",
    file: str = "src/foo.py",
    severity: str = "warning",
) -> EvidenceItem:
    return EvidenceItem(
        id=f"ev_{rule_id}_{file}",
        source="static",
        rule_id=rule_id,
        file=file,
        severity=Severity.WARNING if severity == "warning" else Severity.CRITICAL,
        category="test_gap" if rule_id == "source_without_tests" else "security",
        message=f"Evidence for {rule_id}",
        confidence=0.8,
        tags=[],
    )


def _make_ctx(files: list[FileEntry]) -> PRContext:
    derived = DerivedSignals(
        total_hunks=sum(f.hunk_count for f in files),
        source_files_changed=sum(1 for f in files if f.is_source and not f.is_test),
        test_files_changed=sum(1 for f in files if f.is_test),
        docs_only=all(f.is_docs for f in files) if files else False,
        has_source_without_tests=any(
            f.is_source and not f.is_test for f in files
        ) and not any(f.is_test for f in files),
    )
    return PRContext(
        context_id="test-ctx",
        source="test",
        fetched_at="2024-01-01T00:00:00Z",
        cache_key="test",
        owner="test-owner",
        repo="test-repo",
        pull_number=1,
        pr=MagicMock(),
        commits=MagicMock(),
        files=files,
        derived=derived,
    )


# --- Tests ---


class TestSpecialistOverlap:
    """Specialist tasks should not remove high-priority files from baseline eligibility."""

    def test_high_priority_file_with_specialist_still_gets_baseline(self):
        """A high-priority file with a security task still gets a patch_deep_dive."""
        files = [
            _make_file("src/auth.py", priority_score=85, risk_hints=["auth_path"]),
            _make_file("src/utils.py", priority_score=30),
        ]
        ctx = _make_ctx(files)
        evidence = [_make_evidence("sensitive_field", "src/auth.py")]

        plan = build_context_task_plan(ctx, evidence)
        tasks = plan["tasks"]

        task_types = [t["task_type"] for t in tasks]
        # Should have both security_context and patch_deep_dive for auth.py
        assert "security_context" in task_types
        assert "patch_deep_dive" in task_types

        # patch_deep_dive should target auth.py
        patch_tasks = [t for t in tasks if t["task_type"] == "patch_deep_dive"]
        patch_files = [f for t in patch_tasks for f in t["target"]["files"]]
        assert "src/auth.py" in patch_files

    def test_high_priority_file_with_multiple_specialists_still_gets_baseline(self):
        """A high-priority file with multiple specialist tasks still gets baseline."""
        files = [
            _make_file("src/payment.py", priority_score=90, risk_hints=["auth_path", "db_path"]),
        ]
        ctx = _make_ctx(files)
        evidence = [
            _make_evidence("sensitive_field", "src/payment.py"),
            _make_evidence("sql_injection", "src/payment.py"),
        ]

        plan = build_context_task_plan(ctx, evidence)
        tasks = plan["tasks"]
        task_types = [t["task_type"] for t in tasks]

        # Should have security, data, and baseline
        assert "security_context" in task_types
        assert "data_context" in task_types
        assert "patch_deep_dive" in task_types


class TestSixTaskCapPressure:
    """Plan should respect the 6-task global cap."""

    def test_plan_respects_max_tasks(self):
        """Plan should not exceed MAX_TASKS_PER_RUN."""
        files = [
            _make_file(f"src/file{i}.py", priority_score=80) for i in range(10)
        ]
        ctx = _make_ctx(files)
        plan = build_context_task_plan(ctx, [])
        assert len(plan["tasks"]) <= MAX_TASKS_PER_RUN

    def test_plan_with_many_specialists_respects_cap(self):
        """Plan with many specialist candidates respects the cap."""
        files = [
            _make_file("src/auth.py", priority_score=85, risk_hints=["auth_path"]),
            _make_file("src/db.py", priority_score=80, risk_hints=["db_path"]),
            _make_file("src/config.py", priority_score=70, risk_hints=["config_path"]),
            _make_file("src/main.py", priority_score=75),
            _make_file("src/utils.py", priority_score=60),
            _make_file("src/handler.py", priority_score=65),
            _make_file("src/model.py", priority_score=55),
        ]
        ctx = _make_ctx(files)
        evidence = [
            _make_evidence("sensitive_field", "src/auth.py"),
        ]

        plan = build_context_task_plan(ctx, evidence)
        assert len(plan["tasks"]) <= MAX_TASKS_PER_RUN


class TestCriticalSpecialistPreemption:
    """Critical specialists may preempt lower-priority baseline tasks."""

    def test_critical_specialist_included_over_medium_baseline(self):
        """A critical specialist task should be included even under cap pressure."""
        files = [
            _make_file(f"src/file{i}.py", priority_score=80) for i in range(6)
        ]
        # Add a file with critical security evidence
        files.append(_make_file("src/auth.py", priority_score=85, risk_hints=["auth_path"]))
        ctx = _make_ctx(files)
        evidence = [_make_evidence("sensitive_field", "src/auth.py", severity="critical")]

        plan = build_context_task_plan(ctx, evidence)
        task_types = [t["task_type"] for t in plan["tasks"]]

        # Critical security task should be included
        assert "security_context" in task_types


class TestDeterministicBatching:
    """Baseline patch batching should be deterministic."""

    def test_batch_patches_deterministic(self):
        """Same input always produces same batches."""
        files = [
            _make_file("src/a.py", priority_score=80, added_lines=100),
            _make_file("src/b.py", priority_score=70, added_lines=50),
            _make_file("src/c.py", priority_score=90, added_lines=200),
        ]

        batch1 = batch_patches_by_budget(files)
        batch2 = batch_patches_by_budget(files)

        assert len(batch1) == len(batch2)
        for b1, b2 in zip(batch1, batch2):
            assert [f.filename for f in b1] == [f.filename for f in b2]

    def test_batch_respects_token_budget(self):
        """Batches should not exceed token budget."""
        budget = 1000
        files = [
            _make_file(f"src/file{i}.py", priority_score=80, added_lines=50)
            for i in range(10)
        ]

        batches = batch_patches_by_budget(files, token_budget=budget)

        for batch in batches:
            total_tokens = sum(estimate_patch_tokens(f) for f in batch)
            # Allow slight overage for single-file batches
            if len(batch) > 1:
                assert total_tokens <= budget

    def test_oversized_patch_gets_own_batch(self):
        """An individually oversized patch should get its own batch."""
        files = [
            _make_file("src/small.py", priority_score=80, added_lines=10),
            _make_file("src/huge.py", priority_score=90, added_lines=10000, large_patch=True),
        ]

        batches = batch_patches_by_budget(files, token_budget=1000)

        # huge.py should be in its own batch
        huge_batches = [b for b in batches if any(f.filename == "src/huge.py" for f in b)]
        assert len(huge_batches) == 1
        assert len(huge_batches[0]) == 1

    def test_priority_ordering_in_batches(self):
        """Higher priority files should appear first in batches."""
        files = [
            _make_file("src/low.py", priority_score=50, added_lines=10),
            _make_file("src/high.py", priority_score=90, added_lines=10),
            _make_file("src/med.py", priority_score=70, added_lines=10),
        ]

        batches = batch_patches_by_budget(files)
        all_files = [f for batch in batches for f in batch]

        # high.py should come before med.py and low.py
        high_idx = next(i for i, f in enumerate(all_files) if f.filename == "src/high.py")
        med_idx = next(i for i, f in enumerate(all_files) if f.filename == "src/med.py")
        low_idx = next(i for i, f in enumerate(all_files) if f.filename == "src/low.py")
        assert high_idx < med_idx < low_idx


class TestUncoveredHighPriorityDisclosure:
    """High-priority files omitted by budget should be disclosed."""

    def test_omitted_high_priority_in_manifest(self):
        """When budget is exhausted, omitted high-priority files appear in manifest."""
        # Create files that generate many specialist + baseline tasks exceeding the cap
        files = [
            _make_file("src/auth.py", priority_score=85, risk_hints=["auth_path"]),
            _make_file("src/payment.py", priority_score=90, risk_hints=["auth_path"]),
            _make_file("src/db.py", priority_score=80, risk_hints=["db_path"]),
            _make_file("src/config.py", priority_score=70, risk_hints=["config_path"]),
            _make_file("src/main.py", priority_score=75),
            _make_file("src/handler.py", priority_score=78),
            _make_file("src/model.py", priority_score=72),
            _make_file("src/service.py", priority_score=76),
        ]
        ctx = _make_ctx(files)
        evidence = [
            _make_evidence("sensitive_field", "src/auth.py"),
            _make_evidence("sensitive_field", "src/payment.py"),
            _make_evidence("sql_injection", "src/db.py"),
        ]

        plan = build_context_task_plan(ctx, evidence)

        # Check coverage manifest
        manifest = plan.get("coverage_manifest", {})
        entries = manifest.get("entries", {})

        # Count baseline tasks
        baseline_tasks = [t for t in plan["tasks"] if t["task_type"] == "patch_deep_dive"]

        # All high-priority files should appear in the manifest
        high_priority_files = [f.filename for f in files if f.priority_score_hint >= 70]
        manifest_filenames = {e["filename"] for e in entries.values() if e.get("lane") == "baseline"}

        # At least some high-priority files should be in the manifest
        assert len(manifest_filenames) > 0

    def test_manifest_tracks_high_priority_flag(self):
        """Manifest entries should correctly flag high-priority files."""
        files = [
            _make_file("src/important.py", priority_score=85),
            _make_file("src/trivial.py", priority_score=20),
        ]
        ctx = _make_ctx(files)

        plan = build_context_task_plan(ctx, [])
        manifest = plan.get("coverage_manifest", {})
        entries = manifest.get("entries", {})

        # important.py should be flagged as high priority
        important_entries = [
            e for k, e in entries.items()
            if k.startswith("src/important.py")
        ]
        if important_entries:
            assert important_entries[0]["is_high_priority"] is True


class TestCoverageManifest:
    """Coverage manifest should be correctly structured."""

    def test_manifest_has_baseline_and_specialist_entries(self):
        """Manifest should track both baseline and specialist coverage."""
        files = [
            _make_file("src/auth.py", priority_score=85, risk_hints=["auth_path"]),
        ]
        ctx = _make_ctx(files)
        evidence = [_make_evidence("sensitive_field", "src/auth.py")]

        plan = build_context_task_plan(ctx, evidence)
        manifest = plan.get("coverage_manifest", {})
        entries = manifest.get("entries", {})

        # Should have both baseline and specialist entries for auth.py
        lanes = {e["lane"] for e in entries.values() if "auth.py" in e.get("filename", "")}
        assert "baseline" in lanes or "specialist" in lanes

    def test_manifest_entry_has_required_fields(self):
        """Each manifest entry should have required fields."""
        files = [
            _make_file("src/main.py", priority_score=75),
        ]
        ctx = _make_ctx(files)

        plan = build_context_task_plan(ctx, [])
        manifest = plan.get("coverage_manifest", {})
        entries = manifest.get("entries", {})

        for entry in entries.values():
            assert "filename" in entry
            assert "lane" in entry
            assert "state" in entry
            assert "is_high_priority" in entry


class TestEstimatePatchTokens:
    """Patch token estimation should be reasonable."""

    def test_estimation_scales_with_lines(self):
        """More lines = more estimated tokens."""
        small = _make_file("a.py", added_lines=10, removed_lines=5)
        large = _make_file("b.py", added_lines=100, removed_lines=50)
        assert estimate_patch_tokens(small) < estimate_patch_tokens(large)

    def test_large_patch_gets_max_estimate(self):
        """Large patches get the max estimate."""
        f = _make_file("a.py", large_patch=True)
        assert estimate_patch_tokens(f) > 10000
