"""Tests for normalized final review result aggregation."""
from __future__ import annotations

import pytest

from backend.agent.runtime.final_result import (
    FinalReviewResult,
    NormalizedFinding,
    TaskSummary,
    _deduplicate_findings,
    _finding_fingerprint,
    _merge_unique,
    build_final_result,
    build_fallback_result,
)


# --- Task 5.1: Define normalized completed review result model ---

def test_final_review_result_to_dict():
    result = FinalReviewResult(
        status="completed",
        summary="All good",
        findings=[NormalizedFinding(claim="test", severity="low", fingerprint="abc")],
        uncertainties=["unc1"],
        notes=["note1"],
        task_summaries=[TaskSummary(task_id="t1", execution_status="ok")],
        raw_output="raw text",
        steps=3,
        stopped_by_max_steps=False,
        token_usage={"input_tokens": 100, "output_tokens": 50},
    )
    d = result.to_dict()
    assert d["status"] == "completed"
    assert d["summary"] == "All good"
    assert len(d["findings"]) == 1
    assert d["findings"][0]["claim"] == "test"
    assert d["uncertainties"] == ["unc1"]
    assert d["notes"] == ["note1"]
    assert len(d["task_summaries"]) == 1
    assert "raw_output" not in d  # excluded from frontend payload
    assert d["steps"] == 3
    assert d["token_usage"]["input_tokens"] == 100

    # to_debug_dict includes raw_output
    dd = result.to_debug_dict()
    assert dd["raw_output"] == "raw text"


# --- Task 5.2: Define task summary model ---

def test_task_summary_to_dict():
    ts = TaskSummary(
        task_id="t1",
        task_type="security_context",
        agent_type="security-agent",
        child_session_id="child_123",
        execution_status="ok",
        parse_status="valid",
        validation_errors=[],
    )
    d = ts.to_dict()
    assert d["task_id"] == "t1"
    assert d["task_type"] == "security_context"
    assert d["execution_status"] == "ok"


def test_task_summary_with_validation_errors():
    ts = TaskSummary(
        task_id="t1",
        execution_status="ok",
        parse_status="invalid",
        validation_errors=["missing claim"],
    )
    d = ts.to_dict()
    assert d["validation_errors"] == ["missing claim"]


# --- Task 5.3: Deterministic finding fingerprint ---

def test_finding_fingerprint_deterministic():
    fp1 = _finding_fingerprint("claim", "high", ["file.py:10"])
    fp2 = _finding_fingerprint("claim", "high", ["file.py:10"])
    assert fp1 == fp2


def test_finding_fingerprint_different_claims():
    fp1 = _finding_fingerprint("claim1", "high", ["file.py:10"])
    fp2 = _finding_fingerprint("claim2", "high", ["file.py:10"])
    assert fp1 != fp2


def test_finding_fingerprint_different_severity():
    fp1 = _finding_fingerprint("claim", "high", ["file.py:10"])
    fp2 = _finding_fingerprint("claim", "low", ["file.py:10"])
    assert fp1 != fp2


def test_finding_fingerprint_order_independent():
    fp1 = _finding_fingerprint("claim", "high", ["a.py:1", "b.py:2"])
    fp2 = _finding_fingerprint("claim", "high", ["b.py:2", "a.py:1"])
    assert fp1 == fp2


# --- Task 5.4: Promote findings from valid results ---

def test_build_final_result_promotes_valid_findings():
    task_results = [
        {
            "task_id": "t1",
            "task_type": "security",
            "agent_type": "security-agent",
            "status": "ok",
            "parse_status": "valid",
            "parsed_result": {
                "status": "success",
                "summary": "found issues",
                "findings": [
                    {
                        "claim": "SQL injection risk",
                        "confidence": 0.9,
                        "severity": "critical",
                        "evidence": [{"file": "db.py", "line": 42, "snippet": "query", "source": "file"}],
                    },
                ],
                "uncertainties": ["could not access config"],
                "notes": ["checked main modules"],
            },
        },
    ]
    result = build_final_result(task_results=task_results)
    assert len(result.findings) == 1
    assert result.findings[0].claim == "SQL injection risk"
    assert result.findings[0].severity == "critical"


# --- Task 5.5: Deduplicate findings ---

def test_deduplicate_findings_same_fingerprint():
    f1 = NormalizedFinding(claim="test", severity="high", fingerprint="abc", evidence=[{"file": "a.py"}])
    f2 = NormalizedFinding(claim="test", severity="high", fingerprint="abc", evidence=[{"file": "b.py"}])
    result = _deduplicate_findings([f1, f2])
    assert len(result) == 1
    assert len(result[0].evidence) == 2


def test_deduplicate_findings_different_fingerprint():
    f1 = NormalizedFinding(claim="test1", severity="high", fingerprint="abc")
    f2 = NormalizedFinding(claim="test2", severity="low", fingerprint="def")
    result = _deduplicate_findings([f1, f2])
    assert len(result) == 2


# --- Task 5.6: Merge uncertainties and notes ---

def test_merge_unique_deduplicates():
    result = _merge_unique(["a", "b"], ["b", "c"])
    assert result == ["a", "b", "c"]


def test_merge_unique_max_items():
    result = _merge_unique(["a"], ["b", "c", "d"], max_items=3)
    assert len(result) == 3


def test_merge_unique_skips_empty():
    result = _merge_unique(["a"], ["", "b"])
    assert result == ["a", "b"]


# --- Task 5.7: Invalid task preserves status in summaries ---

def test_build_final_result_invalid_task_no_findings_promoted():
    task_results = [
        {
            "task_id": "t1",
            "task_type": "security",
            "agent_type": "security-agent",
            "status": "ok",
            "parse_status": "invalid",
            "validation_errors": ["missing claim"],
            "parsed_result": {
                "status": "success",
                "summary": "bad format",
                "findings": [
                    {
                        "claim": "should not be promoted",
                        "confidence": 0.9,
                        "severity": "high",
                        "evidence": [],
                    },
                ],
                "uncertainties": ["unc from invalid"],
            },
        },
    ]
    result = build_final_result(task_results=task_results)
    assert len(result.findings) == 0
    assert len(result.task_summaries) == 1
    assert result.task_summaries[0].parse_status == "invalid"
    assert result.task_summaries[0].validation_errors == ["missing claim"]
    # Uncertainties from invalid tasks should still be merged
    assert "unc from invalid" in result.uncertainties


# --- Task 5.9: Fallback for malformed main synthesis ---

def test_build_fallback_result():
    task_results = [
        {
            "task_id": "t1",
            "status": "ok",
            "parse_status": "valid",
            "parsed_result": {
                "status": "success",
                "summary": "found issue",
                "findings": [
                    {
                        "claim": "bug found",
                        "confidence": 0.8,
                        "severity": "medium",
                        "evidence": [{"file": "x.py", "line": 1}],
                    },
                ],
            },
        },
    ]
    result = build_fallback_result(
        raw_output="Some malformed text",
        task_results=task_results,
        steps=2,
        token_usage={"input_tokens": 100, "output_tokens": 50},
    )
    assert result.status == "completed"
    assert len(result.findings) == 1
    assert result.raw_output == "Some malformed text"


# --- Task 5.10: Preserve raw_output ---

def test_build_final_result_preserves_raw_output():
    result = build_final_result(raw_output="main agent text", steps=1)
    assert result.raw_output == "main agent text"


# --- Task 5.1: Complete result shape ---

def test_build_final_result_shape():
    result = build_final_result(
        task_results=[],
        raw_output="text",
        steps=2,
        stopped_by_max_steps=False,
        token_usage={"input_tokens": 50, "output_tokens": 25},
    )
    d = result.to_dict()
    assert "status" in d
    assert "summary" in d
    assert "findings" in d
    assert "uncertainties" in d
    assert "notes" in d
    assert "task_summaries" in d
    assert "raw_output" not in d  # excluded from frontend payload
    assert "steps" in d
    assert "stopped_by_max_steps" in d
    assert "token_usage" in d


def test_build_final_result_max_steps():
    result = build_final_result(stopped_by_max_steps=True)
    assert "max steps" in result.summary.lower()


def test_normalized_finding_to_dict():
    f = NormalizedFinding(
        claim="test",
        confidence=0.9,
        severity="high",
        evidence=[{"file": "x.py", "line": 10, "snippet": "code", "source": "file"}],
        fingerprint="abc123",
    )
    d = f.to_dict()
    assert d["claim"] == "test"
    assert d["fingerprint"] == "abc123"
    assert len(d["evidence"]) == 1
