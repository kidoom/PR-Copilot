from __future__ import annotations

import os
from pathlib import Path

IGNORED_DIRECTORIES = frozenset({
    ".git", "node_modules", "dist", "build", "coverage", ".venv", "venv",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".tox", ".eggs",
    "*.egg-info", ".next", ".nuxt", "target", "vendor",
})

SENSITIVE_FILE_PATTERNS = (
    ".env", ".env.", "private_key", "private-key", "id_rsa", "id_ed25519",
    ".pem", ".key", "credentials", "secret", ".secret",
)

DEFAULT_MAX_LINES = 50
DEFAULT_MAX_BYTES = 10_000
DEFAULT_MAX_SEARCH_RESULTS = 50
DEFAULT_MAX_FILE_LIST_RESULTS = 100


def normalize_repo_root(path: str) -> str:
    return str(Path(path).resolve())


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
