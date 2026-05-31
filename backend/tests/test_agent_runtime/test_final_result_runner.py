from __future__ import annotations

import json
import pytest

from backend.agent.runtime.final_result import (
    FinalReviewResult,
    NormalizedFinding,
    TaskSummary,
    build_final_result,
    build_fallback_result,
)


@pytest.mark.asyncio
async def test_final_result_contains_all_fields():
    result = build_final_result(
        task_results=[],
        raw_output="test output",
        steps=2,
        stopped_by_max_steps=False,
        token_usage={"input_tokens": 100, "output_tokens": 50},
    )
    d = result.to_dict()
    for key in ("status", "summary", "findings", "uncertainties", "notes",
                "task_summaries", "raw_output", "steps", "stopped_by_max_steps", "token_usage"):
        assert key in d, f"Missing key: {key}"


def test_final_result_promotes_only_valid_findings():
    task_results = [
        {
            "task_id": "t1", "status": "ok", "parse_status": "valid",
            "parsed_result": {
                "status": "success", "summary": "ok",
                "findings": [{"claim": "valid claim", "confidence": 0.9, "severity": "high",
                              "evidence": [{"file": "x.py", "line": 1}]}],
                "uncertainties": [], "notes": [],
            },
        },
        {
            "task_id": "t2", "status": "ok", "parse_status": "invalid",
            "validation_errors": ["bad format"],
            "parsed_result": {
                "status": "success", "summary": "bad",
                "findings": [{"claim": "invalid claim", "confidence": 0.5, "severity": "low",
                              "evidence": []}],
                "uncertainties": [], "notes": [],
            },
        },
    ]
    result = build_final_result(task_results=task_results)
    claims = [f.claim for f in result.findings]
    assert "valid claim" in claims
    assert "invalid claim" not in claims


def test_final_result_deduplicates_findings():
    task_results = [
        {
            "task_id": "t1", "status": "ok", "parse_status": "valid",
            "parsed_result": {
                "status": "success", "summary": "ok",
                "findings": [{"claim": "same claim", "confidence": 0.9, "severity": "high",
                              "evidence": [{"file": "x.py", "line": 1}]}],
                "uncertainties": [], "notes": [],
            },
        },
        {
            "task_id": "t2", "status": "ok", "parse_status": "valid",
            "parsed_result": {
                "status": "success", "summary": "ok",
                "findings": [{"claim": "same claim", "confidence": 0.9, "severity": "high",
                              "evidence": [{"file": "x.py", "line": 1}]}],
                "uncertainties": [], "notes": [],
            },
        },
    ]
    result = build_final_result(task_results=task_results)
    assert len(result.findings) == 1


def test_final_result_preserves_raw_output():
    result = build_final_result(raw_output="main agent text", steps=1)
    assert result.raw_output == "main agent text"


def test_final_result_fallback():
    result = build_fallback_result(
        raw_output="malformed",
        task_results=[{
            "task_id": "t1", "status": "ok", "parse_status": "valid",
            "parsed_result": {
                "status": "success", "summary": "ok",
                "findings": [{"claim": "x", "confidence": 0.5, "severity": "low",
                              "evidence": [{"file": "a.py"}]}],
                "uncertainties": [], "notes": [],
            },
        }],
    )
    assert result.status == "completed"
    assert len(result.findings) == 1


def test_final_result_empty_task_results():
    result = build_final_result(task_results=[], raw_output="done")
    assert result.status == "completed"
    assert result.findings == []
    assert result.task_summaries == []
