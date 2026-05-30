"""Tests for structured subagent review result."""
from __future__ import annotations

import json
import pytest

from backend.agent.runtime.review_result import (
    EvidenceRef,
    Finding,
    ReviewResult,
    ReviewStatus,
    parse_review_result,
    validate_review_result,
)


class TestReviewResult:
    def test_create_review_result(self):
        result = ReviewResult(
            status=ReviewStatus.SUCCESS,
            summary="Found security issues",
            findings=[
                Finding(
                    claim="Hardcoded secret detected",
                    confidence=0.9,
                    severity="high",
                    evidence=[EvidenceRef(file="config.py", line=42, snippet="API_KEY = '123'")],
                ),
            ],
            uncertainties=["Could not access private repo"],
            notes=["Checked all public files"],
        )

        assert result.status == ReviewStatus.SUCCESS
        assert len(result.findings) == 1
        assert result.findings[0].claim == "Hardcoded secret detected"

    def test_to_dict(self):
        result = ReviewResult(
            status=ReviewStatus.SUCCESS,
            summary="Test summary",
            findings=[Finding(claim="Test claim", confidence=0.8, severity="medium")],
        )

        d = result.to_dict()
        assert d["status"] == "success"
        assert d["summary"] == "Test summary"
        assert len(d["findings"]) == 1
        assert d["findings"][0]["claim"] == "Test claim"

    def test_from_dict(self):
        data = {
            "status": "success",
            "summary": "Test",
            "findings": [
                {
                    "claim": "Finding 1",
                    "confidence": 0.9,
                    "severity": "high",
                    "evidence": [{"file": "test.py", "line": 10, "snippet": "code", "source": "diff"}],
                },
            ],
            "uncertainties": ["uncertainty 1"],
            "notes": ["note 1"],
        }

        result = ReviewResult.from_dict(data)
        assert result.status == ReviewStatus.SUCCESS
        assert len(result.findings) == 1
        assert result.findings[0].evidence[0].file == "test.py"

    def test_roundtrip(self):
        original = ReviewResult(
            status=ReviewStatus.PARTIAL,
            summary="Partial review",
            findings=[Finding(claim="Test", confidence=0.5, severity="low")],
            uncertainties=["Missing data"],
        )

        d = original.to_dict()
        restored = ReviewResult.from_dict(d)

        assert restored.status == original.status
        assert restored.summary == original.summary
        assert len(restored.findings) == len(original.findings)


class TestParseReviewResult:
    def test_parse_valid_json(self):
        output = json.dumps({
            "status": "success",
            "summary": "Review complete",
            "findings": [],
        })

        result = parse_review_result(output)
        assert result is not None
        assert result.status == ReviewStatus.SUCCESS

    def test_parse_json_in_code_block(self):
        output = '''Here is the review result:

```json
{
    "status": "success",
    "summary": "Review complete",
    "findings": []
}
```

Let me know if you need more details.'''

        result = parse_review_result(output)
        assert result is not None
        assert result.status == ReviewStatus.SUCCESS

    def test_parse_json_with_surrounding_text(self):
        output = '''I completed the review. {"status": "success", "summary": "Done", "findings": []} That's all.'''

        result = parse_review_result(output)
        assert result is not None
        assert result.status == ReviewStatus.SUCCESS

    def test_parse_empty_output(self):
        assert parse_review_result("") is None

    def test_parse_invalid_json(self):
        assert parse_review_result("not json at all") is None

    def test_parse_missing_required_fields(self):
        output = json.dumps({"status": "success"})
        assert parse_review_result(output) is None

    def test_parse_invalid_status(self):
        output = json.dumps({
            "status": "unknown_status",
            "summary": "Test",
        })

        result = parse_review_result(output)
        assert result is not None
        assert result.status == ReviewStatus.ERROR  # Falls back to ERROR


class TestValidateReviewResult:
    def test_valid_result(self):
        result = ReviewResult(
            status=ReviewStatus.SUCCESS,
            summary="Test",
        )
        errors = validate_review_result(result)
        assert errors == []

    def test_missing_summary(self):
        result = ReviewResult(
            status=ReviewStatus.SUCCESS,
            summary="",
        )
        errors = validate_review_result(result)
        assert "summary is required" in errors

    def test_invalid_confidence(self):
        result = ReviewResult(
            status=ReviewStatus.SUCCESS,
            summary="Test",
            findings=[Finding(claim="Test", confidence=1.5, severity="medium")],
        )
        errors = validate_review_result(result)
        assert any("confidence" in e for e in errors)

    def test_invalid_severity(self):
        result = ReviewResult(
            status=ReviewStatus.SUCCESS,
            summary="Test",
            findings=[Finding(claim="Test", confidence=0.5, severity="invalid")],
        )
        errors = validate_review_result(result)
        assert any("severity" in e for e in errors)
