from __future__ import annotations

import os
from pathlib import Path

from .constants import IGNORED_DIRECTORIES, SENSITIVE_PATH_PATTERNS


def safe_resolve_path(repo_root: str, requested_path: str) -> Path:
    root = Path(repo_root).resolve()
    target = (root / requested_path).resolve()
    if not str(target).startswith(str(root)):
        raise ValueError(f"Path traversal rejected: {requested_path}")
    return target


def is_ignored_directory(path: str) -> bool:
    parts = Path(path).parts
    for part in parts:
        if part in IGNORED_DIRECTORIES:
            return True
        for pattern in IGNORED_DIRECTORIES:
            if "*" in pattern and Path(part).match(pattern):
                return True
    return False


def is_sensitive_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    for pattern in SENSITIVE_PATH_PATTERNS:
        if pattern.lower() in normalized:
            return True
        if normalized.endswith(pattern.lower()):
            return True
    return False


def filter_ignored_paths(paths: list[str]) -> list[str]:
    return [p for p in paths if not is_ignored_directory(p)]


def check_budget_exceeded(used: int, limit: int) -> bool:
    return used >= limit
