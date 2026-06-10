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

from backend.agent.runtime.accounting import (
    CoverageManifest,
    CoverageState,
    RuntimeFailureReason,
    RunUsage,
)


# --- Candidate categories ---

ACTIONABLE_CATEGORIES = frozenset({
    "bug_risk",
    "security_risk",
    "behavior_regression",
    "test_gap",
})

CONTEXT_CATEGORIES = frozenset({
    "caller_info",
    "test_coverage",
    "architecture_ref",
    "config_change",
    "data_pattern",
    "runtime_pattern",
})


def classify_candidate(category: str) -> str:
    """Classify a candidate as 'actionable' or 'context'."""
    if category in ACTIONABLE_CATEGORIES:
        return "actionable"
    return "context"


def make_candidate_id(category: str, evidence_locations: list[str], source_ids: list[str] | None = None) -> str:
    """Generate a stable candidate ID from category, evidence, and source identity."""
    sorted_locs = sorted(set(evidence_locations))
    sorted_sources = sorted(set(source_ids or []))
    content = f"{category}|{'|'.join(sorted_locs)}|{'|'.join(sorted_sources)}"
    return f"cand_{hashlib.sha256(content.encode()).hexdigest()[:12]}"


@dataclass
class EvidenceCandidate:
    """A normalized evidence candidate with stable identity."""
    candidate_id: str = ""
    category: str = ""
    classification: str = ""  # actionable or context
    claim: str = ""
    confidence: float = 0.5
    severity: str = "medium"
    evidence: list[dict[str, Any]] = field(default_factory=list)
    source_task_id: str = ""
    source_agent_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "category": self.category,
            "classification": self.classification,
            "claim": self.claim,
            "confidence": self.confidence,
            "severity": self.severity,
            "evidence": self.evidence,
            "source_task_id": self.source_task_id,
            "source_agent_type": self.source_agent_type,
        }


@dataclass
class TaskSummary:
    """Summary of a single SubAgent task execution (task 5.2).

    Accounting fields are optional with defaults for backward compatibility.
    """
    task_id: str = ""
    task_type: str = ""
    agent_type: str = ""
    child_session_id: str = ""
    execution_status: str = ""  # ok, error, cancelled, max_steps
    parse_status: str = ""  # valid, invalid
    validation_errors: list[str] = field(default_factory=list)

    # New accounting fields (optional, backward compatible)
    model_id: str = ""
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    observation_tokens: int = 0
    elapsed_ms: int = 0
    retries: int = 0
    fallback_used: bool = False
    failure_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "agent_type": self.agent_type,
            "child_session_id": self.child_session_id,
            "execution_status": self.execution_status,
            "parse_status": self.parse_status,
            "validation_errors": self.validation_errors,
        }
        # Include accounting fields if present
        if self.model_id:
            result["model_id"] = self.model_id
        if self.model_calls:
            result["model_calls"] = self.model_calls
        if self.input_tokens or self.output_tokens:
            result["input_tokens"] = self.input_tokens
            result["output_tokens"] = self.output_tokens
        if self.observation_tokens:
            result["observation_tokens"] = self.observation_tokens
        if self.elapsed_ms:
            result["elapsed_ms"] = self.elapsed_ms
        if self.retries:
            result["retries"] = self.retries
        if self.fallback_used:
            result["fallback_used"] = self.fallback_used
        if self.failure_reason:
            result["failure_reason"] = self.failure_reason
        return result


@dataclass
class NormalizedFinding:
    """A deduplicated, evidence-backed finding (task 5.5)."""
    claim: str = ""
    confidence: float = 0.5
    severity: str = "medium"
    evidence: list[dict[str, Any]] = field(default_factory=list)
    fingerprint: str = ""
    candidate_id: str = ""
    category: str = ""

    def to_dict(self) -> dict[str, Any]:
        result = {
            "claim": self.claim,
            "confidence": self.confidence,
            "severity": self.severity,
            "evidence": self.evidence,
            "fingerprint": self.fingerprint,
        }
        if self.candidate_id:
            result["candidate_id"] = self.candidate_id
        if self.category:
            result["category"] = self.category
        return result


@dataclass
class FinalReviewResult:
    """Normalized completed review result (task 5.1).

    Coverage and run_usage fields are optional with defaults for backward
    compatibility — older stored results without these fields still load.
    """
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

    # New fields for hybrid review quality guardrails (all optional)
    coverage: CoverageManifest | None = None
    run_usage: RunUsage | None = None
    uncovered_high_priority_paths: list[str] = field(default_factory=list)
    coverage_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for frontend consumption. Excludes raw_output."""
        result: dict[str, Any] = {
            "status": self.status,
            "summary": self.summary,
            "findings": [f.to_dict() for f in self.findings],
            "uncertainties": self.uncertainties,
            "notes": self.notes,
            "task_summaries": [t.to_dict() for t in self.task_summaries],
            "steps": self.steps,
            "stopped_by_max_steps": self.stopped_by_max_steps,
            "token_usage": self.token_usage,
        }
        # Include coverage metadata if present
        if self.coverage:
            result["coverage"] = self.coverage.to_dict()
        if self.coverage_counts:
            result["coverage_counts"] = self.coverage_counts
        if self.uncovered_high_priority_paths:
            result["uncovered_high_priority_paths"] = self.uncovered_high_priority_paths
        if self.run_usage:
            result["run_usage"] = self.run_usage.to_dict()
        return result

    def to_debug_dict(self) -> dict[str, Any]:
        """Serialize with raw_output for backend debugging."""
        result = self.to_dict()
        result["raw_output"] = self.raw_output
        return result


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


def _infer_category(task_type: str, claim: str) -> str:
    """Infer candidate category from task type and claim content."""
    claim_lower = claim.lower()
    if task_type == "security_context" or any(kw in claim_lower for kw in ["auth", "secret", "token", "password", "sql", "injection"]):
        return "security_risk"
    if task_type == "test_context" or "test" in claim_lower:
        return "test_gap"
    if task_type == "runtime_context" or any(kw in claim_lower for kw in ["exception", "error", "timeout", "retry"]):
        return "bug_risk"
    if task_type == "reference_context":
        return "caller_info"
    if task_type == "config_context":
        return "config_change"
    if task_type == "data_context":
        return "data_pattern"
    return "bug_risk"


def _promote_findings_from_task(
    task_output: dict[str, Any],
    task_id: str = "",
    task_type: str = "",
    agent_type: str = "",
) -> tuple[list[NormalizedFinding], list[EvidenceCandidate]]:
    """Promote findings from a valid parsed SubAgent result.

    Returns (findings, candidates) tuple.
    """
    findings = []
    candidates = []
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

        # Create candidate
        category = _infer_category(task_type, claim)
        classification = classify_candidate(category)
        cand_id = make_candidate_id(category, evidence_locations, [task_id])

        candidate = EvidenceCandidate(
            candidate_id=cand_id,
            category=category,
            classification=classification,
            claim=claim,
            confidence=confidence,
            severity=severity,
            evidence=evidence_normalized,
            source_task_id=task_id,
            source_agent_type=agent_type,
        )
        candidates.append(candidate)

        # Only create findings for actionable candidates
        if classification == "actionable":
            findings.append(NormalizedFinding(
                claim=claim,
                confidence=confidence,
                severity=severity,
                evidence=evidence_normalized,
                fingerprint=fp,
                candidate_id=cand_id,
                category=category,
            ))
    return findings, candidates


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
    coverage_manifest: CoverageManifest | None = None,
    run_usage: RunUsage | None = None,
) -> FinalReviewResult:
    """Build a normalized final review result from task results.

    Uses candidate-based model: SubAgent findings become candidates,
    only actionable candidates promoted to findings.

    When coverage_manifest is provided, updates coverage entries based on
    task results and derives final run status from baseline coverage policy.
    """
    all_findings: list[NormalizedFinding] = []
    all_candidates: list[EvidenceCandidate] = []
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

        # Extract accounting from subagent usage if available
        subagent_usage = tr.get("subagent_usage", {})

        task_summaries.append(TaskSummary(
            task_id=task_id,
            task_type=task_type,
            agent_type=agent_type,
            child_session_id=child_session_id,
            execution_status=execution_status,
            parse_status=parse_status,
            validation_errors=validation_errors,
            model_id=subagent_usage.get("model_id", ""),
            model_calls=subagent_usage.get("model_calls", 0),
            input_tokens=subagent_usage.get("input_tokens", 0),
            output_tokens=subagent_usage.get("output_tokens", 0),
            failure_reason=tr.get("failure_reason", ""),
        ))

        # Update coverage manifest if available (task 7.1)
        if coverage_manifest and task_type == "patch_deep_dive":
            _update_coverage_for_baseline_task(
                coverage_manifest, tr, task_id,
            )

        # Only promote findings from valid parsed results
        parsed_result = tr.get("parsed_result")
        if parsed_result is not None and parse_status == "valid":
            promoted_findings, promoted_candidates = _promote_findings_from_task(
                parsed_result,
                task_id=task_id,
                task_type=task_type,
                agent_type=agent_type,
            )
            all_findings.extend(promoted_findings)
            all_candidates.extend(promoted_candidates)

            # Merge uncertainties and notes
            all_uncertainties = _merge_unique(
                all_uncertainties,
                parsed_result.get("uncertainties", []),
            )
            all_notes = _merge_unique(
                all_notes,
                parsed_result.get("notes", []),
            )
        else:
            # Still merge uncertainties from invalid tasks
            if parsed_result:
                all_uncertainties = _merge_unique(
                    all_uncertainties,
                    parsed_result.get("uncertainties", []),
                )

    # Deduplicate findings by fingerprint
    deduplicated = _deduplicate_findings(all_findings)

    # If main_synthesis_parsed is available, merge its findings too
    if main_synthesis_parsed:
        synthesis_findings, synthesis_candidates = _promote_findings_from_task(
            main_synthesis_parsed,
            task_id="main_synthesis",
            task_type="synthesis",
            agent_type="main-agent",
        )
        all_findings.extend(synthesis_findings)
        all_candidates.extend(synthesis_candidates)
        deduplicated = _deduplicate_findings(all_findings)

        all_uncertainties = _merge_unique(
            all_uncertainties,
            main_synthesis_parsed.get("uncertainties", []),
        )
        all_notes = _merge_unique(
            all_notes,
            main_synthesis_parsed.get("notes", []),
        )

    # Promote context-only candidates to notes
    context_candidates = [c for c in all_candidates if c.classification == "context"]
    for cand in context_candidates:
        note = f"[{cand.category}] {cand.claim}"
        all_notes = _merge_unique(all_notes, [note])

    # Build summary
    summary = "Review completed"
    if stopped_by_max_steps:
        summary = "Review completed (max steps reached)"
    if main_synthesis_parsed and main_synthesis_parsed.get("summary"):
        summary = main_synthesis_parsed["summary"]

    # Derive coverage metadata and final status (tasks 7.3, 7.4)
    coverage_counts: dict[str, int] = {}
    uncovered_paths: list[str] = []
    final_status = "completed"

    if coverage_manifest:
        coverage_counts = coverage_manifest.coverage_counts
        uncovered_paths = coverage_manifest.uncovered_high_priority_paths

        # Derive status from baseline coverage policy
        # If any high-priority baseline file is uncovered, status is partial
        if uncovered_paths:
            final_status = "partial"
            summary = f"Review completed with partial baseline coverage ({len(uncovered_paths)} high-priority files uncovered)"

    return FinalReviewResult(
        status=final_status,
        summary=summary,
        findings=deduplicated,
        uncertainties=all_uncertainties,
        notes=all_notes,
        task_summaries=task_summaries,
        raw_output=raw_output,
        steps=steps,
        stopped_by_max_steps=stopped_by_max_steps,
        token_usage=token_usage or {"input_tokens": 0, "output_tokens": 0},
        coverage=coverage_manifest,
        run_usage=run_usage,
        uncovered_high_priority_paths=uncovered_paths,
        coverage_counts=coverage_counts,
    )


def _update_coverage_for_baseline_task(
    manifest: CoverageManifest,
    task_result: dict[str, Any],
    task_id: str,
) -> None:
    """Update coverage entries when a baseline task completes, fails, or times out."""
    execution_status = task_result.get("status", "ok")
    parsed = task_result.get("parsed_result")
    target_files = task_result.get("target_files", [])

    # Try to get target files from the task payload
    if not target_files:
        # Look in the task result for file references
        if parsed and isinstance(parsed, dict):
            for f in parsed.get("findings", []):
                for e in f.get("evidence", []):
                    if e.get("file"):
                        target_files.append(e["file"])

    for filename in target_files:
        entry = manifest.get_entry(filename, CoverageLane.BASELINE.value)
        if not entry:
            continue

        if execution_status == "ok":
            entry.state = CoverageState.REVIEWED.value
        elif execution_status == "error":
            entry.state = CoverageState.FAILED.value
            entry.reason = task_result.get("error", "unknown error")
        elif execution_status == "cancelled":
            entry.state = CoverageState.CANCELLED.value
            entry.reason = "task cancelled"
        elif execution_status == "timeout":
            entry.state = CoverageState.TIMEOUT.value
            entry.reason = "task timed out"
        else:
            entry.state = CoverageState.FAILED.value
            entry.reason = f"unexpected status: {execution_status}"


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
