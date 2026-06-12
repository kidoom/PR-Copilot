"""Tests for PR identity normalization, pr_key generation, and safe path resolvers."""

from __future__ import annotations

import pytest
from pathlib import Path

from backend.storage.pr_session.paths import (
    InvalidIdentityError,
    PathEscapeError,
    build_pr_key,
    normalize_identity,
    normalize_owner,
    normalize_pull_number,
    normalize_repo,
    pr_session_dir,
    pr_sessions_root,
    run_dir,
    run_events_file,
    run_result_file,
    run_state_file,
    runs_dir,
    sessions_root,
)
from backend.storage.pr_session.models import PRIdentity


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


class TestNormalizeOwner:
    def test_lowercase(self):
        assert normalize_owner("OctoCat") == "octocat"

    def test_strips_whitespace(self):
        assert normalize_owner("  octocat  ") == "octocat"


class TestNormalizeRepo:
    def test_lowercase(self):
        assert normalize_repo("Hello-World") == "hello-world"

    def test_strips_whitespace(self):
        assert normalize_repo("  repo  ") == "repo"


class TestNormalizePullNumber:
    def test_positive_int(self):
        assert normalize_pull_number(42) == 42

    def test_string_to_int(self):
        assert normalize_pull_number("7") == 7

    def test_zero_raises(self):
        with pytest.raises(InvalidIdentityError, match="positive"):
            normalize_pull_number(0)

    def test_negative_raises(self):
        with pytest.raises(InvalidIdentityError, match="positive"):
            normalize_pull_number(-1)


class TestNormalizeIdentity:
    def test_normalizes_all_fields(self):
        ident = normalize_identity("OctoCat", "Hello-World", "42")
        assert ident.owner == "octocat"
        assert ident.repo == "hello-world"
        assert ident.pull_number == 42

    def test_empty_owner_raises(self):
        with pytest.raises(InvalidIdentityError, match="owner"):
            normalize_identity("", "repo", 1)

    def test_empty_repo_raises(self):
        with pytest.raises(InvalidIdentityError, match="repo"):
            normalize_identity("owner", "", 1)


# ---------------------------------------------------------------------------
# pr_key generation
# ---------------------------------------------------------------------------


class TestBuildPrKey:
    def test_deterministic(self):
        ident = PRIdentity(owner="octocat", repo="hello-world", pull_number=42)
        k1 = build_pr_key(ident)
        k2 = build_pr_key(ident)
        assert k1 == k2

    def test_contains_owner_repo_number(self):
        ident = PRIdentity(owner="octocat", repo="hello-world", pull_number=42)
        key = build_pr_key(ident)
        assert "octocat" in key
        assert "hello-world" in key
        assert "42" in key

    def test_different_prs_different_keys(self):
        k1 = build_pr_key(PRIdentity("a", "b", 1))
        k2 = build_pr_key(PRIdentity("a", "b", 2))
        assert k1 != k2

    def test_different_repos_different_keys(self):
        k1 = build_pr_key(PRIdentity("a", "b", 1))
        k2 = build_pr_key(PRIdentity("a", "c", 1))
        assert k1 != k2

    def test_has_hash_suffix(self):
        ident = PRIdentity(owner="o", repo="r", pull_number=1)
        key = build_pr_key(ident)
        # key format: owner__repo__number__hash8
        parts = key.split("__")
        assert len(parts) == 4
        assert len(parts[3]) == 8  # 8-char hex hash


# ---------------------------------------------------------------------------
# Safe path resolvers
# ---------------------------------------------------------------------------


class TestSafePathResolvers:
    @pytest.fixture
    def storage_dir(self, tmp_path: Path) -> Path:
        return tmp_path / "storage"

    def test_sessions_root(self, storage_dir: Path):
        assert sessions_root(storage_dir) == storage_dir / "sessions"

    def test_pr_sessions_root(self, storage_dir: Path):
        assert pr_sessions_root(storage_dir) == storage_dir / "sessions" / "pr"

    def test_pr_session_dir(self, storage_dir: Path):
        d = pr_session_dir(storage_dir, "octocat__repo__1__abcdef12")
        assert d == storage_dir / "sessions" / "pr" / "octocat__repo__1__abcdef12"

    def test_runs_dir(self, storage_dir: Path):
        d = runs_dir(storage_dir, "key")
        assert d == storage_dir / "sessions" / "pr" / "key" / "runs"

    def test_run_dir(self, storage_dir: Path):
        d = run_dir(storage_dir, "key", "run-1")
        assert d == storage_dir / "sessions" / "pr" / "key" / "runs" / "run-1"

    def test_run_state_file(self, storage_dir: Path):
        f = run_state_file(storage_dir, "key", "run-1")
        assert f.name == "run.json"
        assert "run-1" in str(f)

    def test_run_events_file(self, storage_dir: Path):
        f = run_events_file(storage_dir, "key", "run-1")
        assert f.name == "events.jsonl"

    def test_run_result_file(self, storage_dir: Path):
        f = run_result_file(storage_dir, "key", "run-1")
        assert f.name == "result.json"


class TestPathTraversalRejection:
    @pytest.fixture
    def storage_dir(self, tmp_path: Path) -> Path:
        return tmp_path / "storage"

    def test_pr_session_dir_rejects_dot_dot(self, storage_dir: Path):
        with pytest.raises(PathEscapeError):
            pr_session_dir(storage_dir, "../../../etc")

    def test_run_dir_rejects_dot_dot(self, storage_dir: Path):
        with pytest.raises(PathEscapeError):
            run_dir(storage_dir, "good-key", "../../../etc")

    def test_pr_session_dir_rejects_absolute_segment(self, storage_dir: Path):
        # A key starting with "/" would try to create an absolute path
        with pytest.raises(PathEscapeError):
            pr_session_dir(storage_dir, "/etc/passwd")
