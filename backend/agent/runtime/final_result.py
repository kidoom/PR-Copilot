"""Normalized final review result aggregation.

Builds a deterministic, frontend-consumable review result from validated
SubAgent outputs. Promotes only evidence-backed findings, deduplicates,
and provides a fallback when main-agent synthesis is malformed.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaskSummary:
    """Summary of a single SubAgent task execution (task 5.2)."""
    task_id: str = ""
    task_type: str = ""
    agent_type: str = ""
    child_session_id: str = ""
    execution_status: str = ""  # ok, error, cancelled, max_steps
    parse_status: str = ""  # valid, invalid
    validation_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "agent_type": self.agent_type,
            "child_session_id": self.child_session_id,
            "execution_status": self.execution_status,
            "parse_status": self.parse_status,
            "validation_errors": self.validation_errors,
        }


@dataclass
class NormalizedFinding:
    """A deduplicated, evidence-backed finding (task 5.5)."""
    claim: str = ""
    confidence: float = 0.5
    severity: str = "medium"
    evidence: list[dict[str, Any]] = field(default_factory=list)
    fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "confidence": self.confidence,
            "severity": self.severity,
            "evidence": self.evidence,
            "fingerprint": self.fingerprint,
        }


@dataclass
class FinalReviewResult:
    """Normalized completed review result (task 5.1)."""
    status: str = "completed"
    summary: str = ""
    findings: list[NormalizedFinding] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    task_summaries: list[TaskSummary] = field(default_factory=list)
    raw_output: str = ""
    steps: int = 0
    stopped_by_max_steps: bool = False
    token_usage: dict[str, int] = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "summary": self.summary,
            "findings": [f.to_dict() for f in self.findings],
            "uncertainties": self.uncertainties,
            "notes": self.notes,
            "task_summaries": [t.to_dict() for t in self.task_summaries],
            "raw_output": self.raw_output,
            "steps": self.steps,
            "stopped_by_max_steps": self.stopped_by_max_steps,
            "token_usage": self.token_usage,
        }


def _finding_fingerprint(claim: str, severity: str, evidence_locations: list[str]) -> str:
    """Generate a deterministic fingerprint for a finding (task 5.3)."""
    normalized_evidence = sorted(set(evidence_locations))
    content = f"{claim}|{severity}|{'|'.join(normalized_evidence)}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _extract_evidence_locations(evidence: list[dict[str, Any]]) -> list[str]:
    """Extract normalized evidence location strings."""
    locations = []
    for e in evidence:
        file = e.get("file", "")
        line = e.get("line")
        if file:
            loc = f"{file}:{line}" if line else file
            locations.append(loc)
    return locations


def _promote_findings_from_task(task_output: dict[str, Any]) -> list[NormalizedFinding]:
    """Promote findings from a valid parsed SubAgent result (task 5.4)."""
    findings = []
    for f in task_output.get("findings", []):
        claim = f.get("claim", "")
        if not claim:
            continue
        severity = f.get("severity", "medium")
        confidence = f.get("confidence", 0.5)
        evidence_raw = f.get("evidence", [])
        evidence_locations = _extract_evidence_locations(evidence_raw)
        fp = _finding_fingerprint(claim, severity, evidence_locations)

        # Normalize evidence refs
        evidence_normalized = []
        for e in evidence_raw:
            evidence_normalized.append({
                "file": e.get("file", ""),
                "line": e.get("line"),
                "snippet": (e.get("snippet", "") or "")[:500],
                "source": e.get("source", ""),
            })

        findings.append(NormalizedFinding(
            claim=claim,
            confidence=confidence,
            severity=severity,
            evidence=evidence_normalized,
            fingerprint=fp,
        ))
    return findings


def _deduplicate_findings(findings: list[NormalizedFinding]) -> list[NormalizedFinding]:
    """Deduplicate findings by fingerprint, preserving evidence (task 5.5)."""
    seen: dict[str, NormalizedFinding] = {}
    for f in findings:
        if f.fingerprint in seen:
            # Merge evidence references
            existing = seen[f.fingerprint]
            existing_locations = {json.dumps(e, sort_keys=True) for e in existing.evidence}
            for e in f.evidence:
                e_json = json.dumps(e, sort_keys=True)
                if e_json not in existing_locations:
                    existing.evidence.append(e)
        else:
            seen[f.fingerprint] = f
    return list(seen.values())


def _merge_unique(items: list[str], new_items: list[str], max_items: int = 50) -> list[str]:
    """Merge bounded unique items (task 5.6)."""
    seen = set(items)
    for item in new_items:
        if item and item not in seen and len(items) < max_items:
            items.append(item)
            seen.add(item)
    return items


def build_final_result(
    *,
    task_results: list[dict[str, Any]] | None = None,
    raw_output: str = "",
    steps: int = 0,
    stopped_by_max_steps: bool = False,
    token_usage: dict[str, int] | None = None,
    main_synthesis_parsed: dict[str, Any] | None = None,
) -> FinalReviewResult:
    """Build a normalized final review result from task results (tasks 5.4-5.10).

    Args:
        task_results: List of per-task results from TaskTool batch dispatch.
        raw_output: Main-agent visible final text.
        steps: Number of main-agent steps.
        stopped_by_max_steps: Whether the main-agent hit max steps.
        token_usage: Token usage dict with input_tokens and output_tokens.
        main_synthesis_parsed: Parsed main-agent synthesis JSON if available.

    Returns:
        A deterministic FinalReviewResult.
    """
    all_findings: list[NormalizedFinding] = []
    all_uncertainties: list[str] = []
    all_notes: list[str] = []
    task_summaries: list[TaskSummary] = []

    for tr in (task_results or []):
        task_id = tr.get("task_id", "")
        task_type = tr.get("task_type", "")
        agent_type = tr.get("agent_type", "")
        child_session_id = tr.get("child_session_id", "")
        execution_status = tr.get("status", "ok")
        parse_status = tr.get("parse_status", "")
        validation_errors = tr.get("validation_errors", [])

        task_summaries.append(TaskSummary(
            task_id=task_id,
            task_type=task_type,
            agent_type=agent_type,
            child_session_id=child_session_id,
            execution_status=execution_status,
            parse_status=parse_status,
            validation_errors=validation_errors,
        ))

        # Only promote findings from valid parsed results (task 5.4)
        parsed_result = tr.get("parsed_result")
        if parsed_result is not None and parse_status == "valid":
            promoted = _promote_findings_from_task(parsed_result)
            all_findings.extend(promoted)

            # Merge uncertainties and notes (task 5.6)
            all_uncertainties = _merge_unique(
                all_uncertainties,
                parsed_result.get("uncertainties", []),
            )
            all_notes = _merge_unique(
                all_notes,
                parsed_result.get("notes", []),
            )
        else:
            # Preserve invalid task status in summaries (task 5.7)
            # Still merge uncertainties from invalid tasks
            if parsed_result:
                all_uncertainties = _merge_unique(
                    all_uncertainties,
                    parsed_result.get("uncertainties", []),
                )

    # Deduplicate findings (task 5.5)
    deduplicated = _deduplicate_findings(all_findings)

    # If main_synthesis_parsed is available, merge its findings too
    if main_synthesis_parsed:
        synthesis_findings = _promote_findings_from_task(main_synthesis_parsed)
        all_findings.extend(synthesis_findings)
        deduplicated = _deduplicate_findings(all_findings)

        all_uncertainties = _merge_unique(
            all_uncertainties,
            main_synthesis_parsed.get("uncertainties", []),
        )
        all_notes = _merge_unique(
            all_notes,
            main_synthesis_parsed.get("notes", []),
        )

    # Build summary
    summary = "Review completed"
    if stopped_by_max_steps:
        summary = "Review completed (max steps reached)"
    if main_synthesis_parsed and main_synthesis_parsed.get("summary"):
        summary = main_synthesis_parsed["summary"]

    return FinalReviewResult(
        status="completed",
        summary=summary,
        findings=deduplicated,
        uncertainties=all_uncertainties,
        notes=all_notes,
        task_summaries=task_summaries,
        raw_output=raw_output,
        steps=steps,
        stopped_by_max_steps=stopped_by_max_steps,
        token_usage=token_usage or {"input_tokens": 0, "output_tokens": 0},
    )


def build_fallback_result(
    *,
    raw_output: str = "",
    task_results: list[dict[str, Any]] | None = None,
    steps: int = 0,
    stopped_by_max_steps: bool = False,
    token_usage: dict[str, int] | None = None,
) -> FinalReviewResult:
    """Build a deterministic fallback when main-agent synthesis is malformed (task 5.9)."""
    return build_final_result(
        task_results=task_results,
        raw_output=raw_output,
        steps=steps,
        stopped_by_max_steps=stopped_by_max_steps,
        token_usage=token_usage,
        main_synthesis_parsed=None,
    )
