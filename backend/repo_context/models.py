from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class VerificationStatus(str, Enum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    FAILED = "failed"


@dataclass
class RepoVerificationState:
    status: VerificationStatus = VerificationStatus.UNVERIFIED
    owner: str = ""
    repo: str = ""
    head_sha: str = ""
    reason: str = ""
    repo_root: str = ""


@dataclass
class ToolUsage:
    searches_used: int = 0
    files_read: int = 0
    tokens_output: int = 0


class PackageStatus(str, Enum):
    FOUND_CONTEXT = "found_context"
    INCONCLUSIVE = "inconclusive"
    BLOCKED = "blocked"
    ERROR = "error"


@dataclass
class ContextEvidenceRef:
    file: str = ""
    line: int | None = None
    snippet: str = ""
    source: str = ""


@dataclass
class ContextFinding:
    claim: str
    confidence: float = 0.0
    evidence: list[ContextEvidenceRef] = field(default_factory=list)


@dataclass
class ContextEvidencePackage:
    task_id: str
    task_type: str
    status: PackageStatus
    findings: list[ContextFinding] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    tool_usage: ToolUsage = field(default_factory=ToolUsage)


@dataclass
class RepoContextSession:
    context_id: str
    task_id: str
    task_type: str = ""
    repo_root: str = ""
    verification: RepoVerificationState = field(default_factory=RepoVerificationState)
    budget_searches: int = 5
    budget_files: int = 10
    budget_tokens: int = 3000
    usage: ToolUsage = field(default_factory=ToolUsage)
    package_submitted: bool = False
    final_package: ContextEvidencePackage | None = None
    todos: list[dict] = field(default_factory=list)
