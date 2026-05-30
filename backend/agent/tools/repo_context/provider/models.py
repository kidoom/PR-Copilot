from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class WorkspaceSource(str, Enum):
    LOCAL = "local"
    TEMP_CLONE = "temp_clone"


@dataclass
class PRIdentity:
    owner: str
    repo: str
    head_sha: str
    pull_number: int | None = None
    base_ref: str = "main"


@dataclass
class RepoWorkspace:
    run_id: str
    context_id: str
    repo_root: str
    source: WorkspaceSource
    pr_identity: PRIdentity | None = None
    is_temp: bool = False


class PreparationErrorKind(str, Enum):
    MISSING_PR_IDENTITY = "missing_pr_identity"
    LOCAL_MISMATCH = "local_mismatch"
    CLONE_FAILED = "clone_failed"
    CHECKOUT_FAILED = "checkout_failed"
    AUTH_FAILED = "auth_failed"
    UNKNOWN = "unknown"


@dataclass
class PreparationError:
    kind: PreparationErrorKind
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class PreparationResult:
    workspace: RepoWorkspace | None = None
    error: PreparationError | None = None

    @property
    def ok(self) -> bool:
        return self.workspace is not None and self.error is None

    @staticmethod
    def success(workspace: RepoWorkspace) -> PreparationResult:
        return PreparationResult(workspace=workspace)

    @staticmethod
    def failure(error: PreparationError) -> PreparationResult:
        return PreparationResult(error=error)
