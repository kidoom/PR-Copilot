from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# --- Constants ---

IGNORED_DIRECTORIES = frozenset({
    ".git", "node_modules", "dist", "build", "coverage", ".venv", "venv",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".tox", ".eggs",
    "*.egg-info", ".next", ".nuxt", "target", "vendor",
})

SENSITIVE_FILE_PATTERNS = (
    ".env", ".env.", "private_key", "private-key", "id_rsa", "id_ed25519",
    ".pem", ".key", "credentials", "secret", ".secret",
)

MAX_SEARCH_RESULTS = 50
MAX_SNIPPET_LINES = 50
MAX_SNIPPET_BYTES = 10_000


# --- Verification State ---


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


# --- Budget and Usage ---


@dataclass
class ToolUsage:
    search_count: int = 0
    file_read_count: int = 0
    approximate_tokens: int = 0


@dataclass
class TaskBudget:
    max_searches: int = 5
    max_files: int = 10
    max_tokens: int = 3000


# --- Context Evidence Package ---


@dataclass
class ContextEvidenceRef:
    file: str = ""
    line: int | None = None
    snippet: str = ""
    source: str = ""


@dataclass
class ContextFinding:
    claim: str
    confidence: float = 0.5
    evidence: list[ContextEvidenceRef] = field(default_factory=list)


class PackageStatus(str, Enum):
    FOUND_CONTEXT = "found_context"
    INCONCLUSIVE = "inconclusive"
    BLOCKED = "blocked"
    ERROR = "error"


@dataclass
class ContextEvidencePackage:
    task_id: str
    task_type: str
    status: PackageStatus
    findings: list[ContextFinding] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    tool_usage: ToolUsage = field(default_factory=ToolUsage)


# --- Session ---


@dataclass
class RepoContextSession:
    context_id: str
    task_id: str
    repo_root: str = ""
    verification: RepoVerificationState = field(default_factory=RepoVerificationState)
    budget: TaskBudget = field(default_factory=TaskBudget)
    usage: ToolUsage = field(default_factory=ToolUsage)
    final_package: ContextEvidencePackage | None = None
    todos: list[dict[str, Any]] = field(default_factory=list)
