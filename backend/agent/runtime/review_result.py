from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ReviewStatus(str, Enum):
    """Status of a subagent review task."""
    SUCCESS = "success"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    ERROR = "error"


@dataclass
class EvidenceRef:
    """Reference to evidence supporting a finding."""
    file: str = ""
    line: int | None = None
    snippet: str = ""
    source: str = ""  # e.g., "diff", "file", "search"

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "line": self.line,
            "snippet": self.snippet[:500] if self.snippet else "",
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceRef:
        return cls(
            file=data.get("file", ""),
            line=data.get("line"),
            snippet=data.get("snippet", "")[:500],
            source=data.get("source", ""),
        )


@dataclass
class Finding:
    """A single finding from the review."""
    claim: str
    confidence: float = 0.5
    severity: str = "medium"  # low, medium, high, critical
    evidence: list[EvidenceRef] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "confidence": self.confidence,
            "severity": self.severity,
            "evidence": [e.to_dict() for e in self.evidence],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Finding:
        return cls(
            claim=data.get("claim", ""),
            confidence=data.get("confidence", 0.5),
            severity=data.get("severity", "medium"),
            evidence=[EvidenceRef.from_dict(e) for e in data.get("evidence", [])],
        )


@dataclass
class ReviewResult:
    """Structured subagent review result.

    This is the integration contract between subagent and TaskTool.
    """
    status: ReviewStatus
    summary: str
    findings: list[Finding] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "summary": self.summary,
            "findings": [f.to_dict() for f in self.findings],
            "uncertainties": self.uncertainties,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReviewResult:
        status_str = data.get("status", "error")
        try:
            status = ReviewStatus(status_str)
        except ValueError:
            status = ReviewStatus.ERROR

        return cls(
            status=status,
            summary=data.get("summary", ""),
            findings=[Finding.from_dict(f) for f in data.get("findings", [])],
            uncertainties=data.get("uncertainties", []),
            notes=data.get("notes", []),
        )


def parse_review_result(output: str) -> ReviewResult | None:
    """Parse a subagent's final output as a structured review result.

    Returns None if the output is not valid JSON or doesn't match the schema.
    """
    if not output:
        return None

    # Try to extract JSON from the output
    # The output might contain markdown or other text around the JSON
    json_str = _extract_json(output)
    if not json_str:
        return None

    try:
        data = json.loads(json_str)
        if not isinstance(data, dict):
            return None

        # Check required fields
        if "status" not in data or "summary" not in data:
            return None

        return ReviewResult.from_dict(data)
    except (json.JSONDecodeError, ValueError):
        return None


def _extract_json(text: str) -> str | None:
    """Extract JSON from text that might contain markdown or other content."""
    # Try direct parse first
    text = text.strip()
    if text.startswith("{"):
        return text

    # Look for JSON in code blocks
    import re
    json_pattern = r'```(?:json)?\s*\n?([\s\S]*?)\n?```'
    matches = re.findall(json_pattern, text)
    for match in matches:
        match = match.strip()
        if match.startswith("{"):
            return match

    # Look for first { to last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]

    return None


def validate_review_result(result: ReviewResult) -> list[str]:
    """Validate a review result and return list of validation errors."""
    errors = []

    if not result.summary:
        errors.append("summary is required")

    if result.status == ReviewStatus.ERROR and not result.summary:
        errors.append("error status requires a summary explaining the error")

    for i, finding in enumerate(result.findings):
        if not finding.claim:
            errors.append(f"finding[{i}].claim is required")
        if not 0 <= finding.confidence <= 1:
            errors.append(f"finding[{i}].confidence must be between 0 and 1")
        if finding.severity not in ("informational", "info", "low", "medium", "high", "critical"):
            errors.append(f"finding[{i}].severity must be informational/info/low/medium/high/critical")
        if not finding.evidence:
            errors.append(f"finding[{i}].evidence is required for actionable findings")
        for j, evidence in enumerate(finding.evidence):
            if not evidence.file:
                errors.append(f"finding[{i}].evidence[{j}].file is required")
            if not evidence.source and not evidence.snippet:
                errors.append(f"finding[{i}].evidence[{j}] requires source or snippet")

    return errors
