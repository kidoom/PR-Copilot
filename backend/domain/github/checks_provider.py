"""Run-scoped GitHub Checks summary provider.

Retrieves and normalizes GitHub check runs and legacy commit status contexts
for a PR head SHA. Provides bounded, cached, non-fatal CI evidence for
PR review context tasks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.agent.runtime.cancellation import CancellationProbe, Cancelled
from backend.domain.github.client import GitHubAPIError, GitHubClient


# Maximum records returned per category
MAX_CHECK_RUNS = 30
MAX_STATUS_CONTEXTS = 30

# Maximum text field lengths
MAX_NAME_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 500
MAX_URL_LENGTH = 500


def _truncate(text: str, max_len: int) -> str:
    if not text or len(text) <= max_len:
        return text or ""
    return text[:max_len] + "...[truncated]"


@dataclass
class CheckRunSummary:
    """Normalized check-run fields (task 7.2)."""
    name: str = ""
    status: str = ""  # queued, in_progress, completed
    conclusion: str = ""  # success, failure, neutral, cancelled, timed_out, action_required, skipped, stale
    details_url: str = ""
    started_at: str = ""
    completed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "conclusion": self.conclusion,
            "details_url": self.details_url,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


@dataclass
class StatusContextSummary:
    """Normalized legacy status fields (task 7.3)."""
    context: str = ""
    state: str = ""  # pending, success, failure, error
    target_url: str = ""
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "context": self.context,
            "state": self.state,
            "target_url": self.target_url,
            "description": self.description,
        }


@dataclass
class ChecksSummary:
    """Normalized GitHub Checks summary (task 7.4)."""
    overall_status: str = ""  # success, failure, pending, unavailable
    check_runs: list[CheckRunSummary] = field(default_factory=list)
    status_contexts: list[StatusContextSummary] = field(default_factory=list)
    total_check_runs: int = 0
    total_status_contexts: int = 0
    truncated: bool = False

    # Summary counts
    success_count: int = 0
    failure_count: int = 0
    pending_count: int = 0
    neutral_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_status": self.overall_status,
            "check_runs": [cr.to_dict() for cr in self.check_runs],
            "status_contexts": [sc.to_dict() for sc in self.status_contexts],
            "total_check_runs": self.total_check_runs,
            "total_status_contexts": self.total_status_contexts,
            "truncated": self.truncated,
            "summary": {
                "success": self.success_count,
                "failure": self.failure_count,
                "pending": self.pending_count,
                "neutral": self.neutral_count,
            },
        }


@dataclass
class ChecksProviderResult:
    """Structured outcome from the checks provider (task 7.8)."""
    status: str = ""  # ok, unavailable, error, cancelled
    summary: ChecksSummary | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        if self.summary:
            result = self.summary.to_dict()
            result["status"] = self.status
            return result
        return {
            "status": self.status,
            "reason": self.reason,
        }


class ChecksSummaryProvider:
    """Run-scoped checks summary provider (task 7.1).

    Bound to server-owned owner, repo, head SHA, and server-held token.
    Caches results per run identity and head SHA.
    """

    def __init__(
        self,
        *,
        owner: str,
        repo: str,
        head_sha: str,
        github_client: GitHubClient,
        cancellation_probe: CancellationProbe | None = None,
    ) -> None:
        self._owner = owner
        self._repo = repo
        self._head_sha = head_sha
        self._client = github_client
        self._cancellation_probe = cancellation_probe
        self._cache: ChecksProviderResult | None = None

    async def get_summary(self) -> ChecksProviderResult:
        """Get the cached or fresh checks summary (task 7.7)."""
        if self._cache is not None:
            return self._cache

        if not self._owner or not self._repo or not self._head_sha:
            result = ChecksProviderResult(status="unavailable", reason="Missing owner, repo, or head SHA")
            self._cache = result
            return result

        try:
            # Fetch check runs
            check_runs_data = await self._client.get_check_runs(
                self._owner, self._repo, self._head_sha
            )

            # Fetch combined status
            status_data = await self._client.get_combined_status(
                self._owner, self._repo, self._head_sha
            )

        except Cancelled:
            result = ChecksProviderResult(status="cancelled", reason="Request was cancelled")
            self._cache = result
            return result

        except GitHubAPIError as e:
            if e.error_category in ("auth", "permission"):
                result = ChecksProviderResult(status="unavailable", reason=f"GitHub access denied: {e.message}")
            elif e.error_category == "rate_limit":
                result = ChecksProviderResult(status="error", reason=f"GitHub rate limit: {e.message}")
            else:
                result = ChecksProviderResult(status="error", reason=f"GitHub API error: {e.message}")
            self._cache = result
            return result

        except Exception as e:
            result = ChecksProviderResult(status="error", reason=f"Unexpected error: {str(e)[:200]}")
            self._cache = result
            return result

        # Normalize check runs (task 7.2)
        raw_check_runs = check_runs_data.get("check_runs", [])
        check_runs: list[CheckRunSummary] = []
        truncated = False

        for cr in raw_check_runs[:MAX_CHECK_RUNS]:
            check_runs.append(CheckRunSummary(
                name=_truncate(cr.get("name", ""), MAX_NAME_LENGTH),
                status=cr.get("status", ""),
                conclusion=cr.get("conclusion", ""),
                details_url=_truncate(cr.get("details_url", ""), MAX_URL_LENGTH),
                started_at=cr.get("started_at", ""),
                completed_at=cr.get("completed_at", ""),
            ))

        if len(raw_check_runs) > MAX_CHECK_RUNS:
            truncated = True

        # Normalize status contexts (task 7.3)
        raw_statuses = status_data.get("statuses", [])
        status_contexts: list[StatusContextSummary] = []

        for sc in raw_statuses[:MAX_STATUS_CONTEXTS]:
            status_contexts.append(StatusContextSummary(
                context=_truncate(sc.get("context", ""), MAX_NAME_LENGTH),
                state=sc.get("state", ""),
                target_url=_truncate(sc.get("target_url", ""), MAX_URL_LENGTH),
                description=_truncate(sc.get("description", "") or "", MAX_DESCRIPTION_LENGTH),
            ))

        if len(raw_statuses) > MAX_STATUS_CONTEXTS:
            truncated = True

        # Compute summary counts (task 7.4)
        success_count = 0
        failure_count = 0
        pending_count = 0
        neutral_count = 0

        for cr in check_runs:
            if cr.conclusion == "success":
                success_count += 1
            elif cr.conclusion in ("failure", "timed_out", "action_required"):
                failure_count += 1
            elif cr.conclusion == "neutral":
                neutral_count += 1
            elif cr.status in ("queued", "in_progress"):
                pending_count += 1

        for sc in status_contexts:
            if sc.state == "success":
                success_count += 1
            elif sc.state in ("failure", "error"):
                failure_count += 1
            elif sc.state == "pending":
                pending_count += 1

        # Determine overall status (task 7.4)
        if failure_count > 0:
            overall_status = "failure"
        elif pending_count > 0:
            overall_status = "pending"
        elif success_count > 0 or neutral_count > 0:
            overall_status = "success"
        else:
            overall_status = "success"  # No checks = success

        summary = ChecksSummary(
            overall_status=overall_status,
            check_runs=check_runs,
            status_contexts=status_contexts,
            total_check_runs=len(raw_check_runs),
            total_status_contexts=len(raw_statuses),
            truncated=truncated,
            success_count=success_count,
            failure_count=failure_count,
            pending_count=pending_count,
            neutral_count=neutral_count,
        )

        result = ChecksProviderResult(status="ok", summary=summary)
        self._cache = result
        return result
