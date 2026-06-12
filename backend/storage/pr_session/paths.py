"""Canonical PR identity normalization and safe filesystem path resolvers.

``pr_key`` is a deterministic, filesystem-safe identifier derived from the
normalized ``(owner, repo, pull_number)`` triple plus a short hash suffix for
collision resistance.  The original identity is always preserved in
``pr.json``; callers never infer identity solely from the directory name.

Every resolver in this module verifies that the resulting path remains under
the configured session storage root before returning it.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from backend.storage.pr_session.models import PRIdentity

# Characters allowed in a single path segment of ``pr_key``.
_SAFE_SEGMENT = re.compile(r"^[a-z0-9][a-z0-9\-]*$")

# Maximum length for a single pr_key directory name.
_MAX_PR_KEY_LEN = 120


class PathEscapeError(Exception):
    """Raised when a resolved path would escape the storage root."""


class InvalidIdentityError(Exception):
    """Raised when a PR identity contains invalid or empty fields."""


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def normalize_owner(owner: str) -> str:
    """Normalize a GitHub owner (user or org) for identity comparison."""
    return owner.strip().lower()


def normalize_repo(repo: str) -> str:
    """Normalize a GitHub repository name for identity comparison."""
    return repo.strip().lower()


def normalize_pull_number(pull_number: int | str) -> int:
    """Normalize and validate a pull request number."""
    num = int(pull_number)
    if num <= 0:
        raise InvalidIdentityError(f"pull_number must be positive, got {num}")
    return num


def normalize_identity(owner: str, repo: str, pull_number: int | str) -> PRIdentity:
    """Return a canonically normalized ``PRIdentity``.

    Raises ``InvalidIdentityError`` if any field is empty or invalid.
    """
    o = normalize_owner(owner)
    r = normalize_repo(repo)
    p = normalize_pull_number(pull_number)
    if not o:
        raise InvalidIdentityError("owner must not be empty")
    if not r:
        raise InvalidIdentityError("repo must not be empty")
    return PRIdentity(owner=o, repo=r, pull_number=p)


# ---------------------------------------------------------------------------
# pr_key generation
# ---------------------------------------------------------------------------


def _short_hash(owner: str, repo: str, pull_number: int) -> str:
    """Produce an 8-char hex hash for collision resistance."""
    digest = hashlib.sha256(f"{owner}/{repo}/{pull_number}".encode()).hexdigest()
    return digest[:8]


def build_pr_key(identity: PRIdentity) -> str:
    """Build a filesystem-safe ``pr_key`` from a normalized identity.

    Format: ``{owner}__{repo}__{pull_number}__{hash8}``

    Each segment is lowercased and non-alphanumeric characters (except
    hyphen) are replaced with hyphens.  The double underscore separates
    logical segments.
    """
    o = re.sub(r"[^a-z0-9-]", "-", identity.owner.lower())
    r = re.sub(r"[^a-z0-9-]", "-", identity.repo.lower())
    h = _short_hash(identity.owner, identity.repo, identity.pull_number)
    key = f"{o}__{r}__{identity.pull_number}__{h}"
    if len(key) > _MAX_PR_KEY_LEN:
        key = key[:_MAX_PR_KEY_LEN]
    return key


# ---------------------------------------------------------------------------
# Safe path resolvers
# ---------------------------------------------------------------------------


def _check_inside(path: Path, root: Path) -> Path:
    """Verify *path* resolves under *root*; raise on escape."""
    resolved = path.resolve()
    root_resolved = root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        raise PathEscapeError(
            f"Path {resolved} escapes storage root {root_resolved}"
        )
    return resolved


def sessions_root(storage_dir: Path) -> Path:
    """Return the ``sessions/`` directory under the storage root."""
    return storage_dir / "sessions"


def pr_sessions_root(storage_dir: Path) -> Path:
    """Return the ``sessions/pr/`` directory."""
    return sessions_root(storage_dir) / "pr"


def pr_session_dir(storage_dir: Path, pr_key: str) -> Path:
    """Return the directory for a specific PR session.

    Raises ``PathEscapeError`` if the resolved path escapes the root.
    """
    path = pr_sessions_root(storage_dir) / pr_key
    return _check_inside(path, pr_sessions_root(storage_dir))


def pr_meta_file(storage_dir: Path, pr_key: str) -> Path:
    """Return the ``pr.json`` path for a PR session."""
    return pr_session_dir(storage_dir, pr_key) / "pr.json"


def pr_index_file(storage_dir: Path, pr_key: str) -> Path:
    """Return the ``index.json`` path for a PR session."""
    return pr_session_dir(storage_dir, pr_key) / "index.json"


def runs_dir(storage_dir: Path, pr_key: str) -> Path:
    """Return the ``runs/`` directory for a PR session."""
    return pr_session_dir(storage_dir, pr_key) / "runs"


def run_dir(storage_dir: Path, pr_key: str, run_id: str) -> Path:
    """Return the directory for a specific run.

    Raises ``PathEscapeError`` if the resolved path escapes the root.
    """
    path = runs_dir(storage_dir, pr_key) / run_id
    return _check_inside(path, runs_dir(storage_dir, pr_key))


def run_state_file(storage_dir: Path, pr_key: str, run_id: str) -> Path:
    """Return the ``run.json`` path for a run."""
    return run_dir(storage_dir, pr_key, run_id) / "run.json"


def run_context_file(storage_dir: Path, pr_key: str, run_id: str) -> Path:
    """Return the ``context.json`` path for a run."""
    return run_dir(storage_dir, pr_key, run_id) / "context.json"


def run_task_plan_file(storage_dir: Path, pr_key: str, run_id: str) -> Path:
    """Return the ``task-plan.json`` path for a run."""
    return run_dir(storage_dir, pr_key, run_id) / "task-plan.json"


def run_events_file(storage_dir: Path, pr_key: str, run_id: str) -> Path:
    """Return the ``events.jsonl`` path for a run."""
    return run_dir(storage_dir, pr_key, run_id) / "events.jsonl"


def run_result_file(storage_dir: Path, pr_key: str, run_id: str) -> Path:
    """Return the ``result.json`` path for a run."""
    return run_dir(storage_dir, pr_key, run_id) / "result.json"


def run_agent_sessions_file(storage_dir: Path, pr_key: str, run_id: str) -> Path:
    """Return the ``agent-sessions.json`` path for a run."""
    return run_dir(storage_dir, pr_key, run_id) / "agent-sessions.json"


def run_temp_file(storage_dir: Path, pr_key: str, run_id: str, suffix: str) -> Path:
    """Return a temporary sibling file path for atomic writes.

    The temp file lives in the same directory as the target so that
    ``os.replace()`` is an atomic rename on the same filesystem.
    """
    import uuid

    d = run_dir(storage_dir, pr_key, run_id)
    return d / f".tmp-{suffix}-{uuid.uuid4().hex[:8]}"


def pr_temp_file(storage_dir: Path, pr_key: str, suffix: str) -> Path:
    """Return a temporary sibling file path for atomic PR-level writes."""
    import uuid

    d = pr_session_dir(storage_dir, pr_key)
    return d / f".tmp-{suffix}-{uuid.uuid4().hex[:8]}"
