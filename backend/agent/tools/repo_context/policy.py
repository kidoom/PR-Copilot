from __future__ import annotations

import os
import re
from pathlib import Path

from backend.agent.tools.repo_context.models import (
    IGNORED_DIRECTORIES,
    SENSITIVE_FILE_PATTERNS,
    RepoContextSession,
    VerificationStatus,
)


def resolve_safe_path(repo_root: str, requested_path: str) -> str | None:
    root = Path(repo_root).resolve()
    target = (root / requested_path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return str(target)


def is_ignored_directory(path: str) -> bool:
    parts = Path(path).parts
    for part in parts:
        if part in IGNORED_DIRECTORIES:
            return True
        for pattern in IGNORED_DIRECTORIES:
            if pattern.startswith("*.") and part.endswith(pattern[1:]):
                return True
    return False


def is_sensitive_file(path: str) -> bool:
    name = os.path.basename(path).lower()
    for pattern in SENSITIVE_FILE_PATTERNS:
        if pattern in name:
            return True
    return False


def check_budget_search(session: RepoContextSession) -> bool:
    return session.usage.search_count < session.budget.max_searches


def consume_search_budget(session: RepoContextSession) -> None:
    session.usage.search_count += 1


def check_budget_file_read(session: RepoContextSession) -> bool:
    return session.usage.file_read_count < session.budget.max_files


def consume_file_read_budget(session: RepoContextSession) -> None:
    session.usage.file_read_count += 1


def check_budget_tokens(session: RepoContextSession, estimated_tokens: int) -> bool:
    return session.usage.approximate_tokens + estimated_tokens <= session.budget.max_tokens


def consume_token_budget(session: RepoContextSession, tokens: int) -> None:
    session.usage.approximate_tokens += tokens


def is_verified(session: RepoContextSession) -> bool:
    return session.verification.status == VerificationStatus.VERIFIED


def require_verification(session: RepoContextSession) -> str | None:
    if not is_verified(session):
        return "Repository context is not verified. Call verify_repo_context first."
    return None
