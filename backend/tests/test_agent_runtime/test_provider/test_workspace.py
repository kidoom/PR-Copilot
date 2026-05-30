from __future__ import annotations

import os
import subprocess
import tempfile

import pytest

from backend.agent.tools.repo_context.provider.models import (
    PRIdentity,
    PreparationErrorKind,
    WorkspaceSource,
)
from backend.agent.tools.repo_context.provider.workspace import (
    RepoWorkspaceManager,
    _verify_local_identity,
    _create_askpass_script,
    _cleanup_askpass,
)


def _init_git_repo(path: str, remote_url: str | None = None, head_sha_prefix: str | None = None) -> str:
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, capture_output=True, check=True)
    with open(os.path.join(path, "README.md"), "w") as f:
        f.write("# Test\n")
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, capture_output=True, check=True)
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True, check=True)
    sha = result.stdout.strip()
    if remote_url:
        subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=path, capture_output=True, check=True)
    return sha


class TestVerifyLocalIdentity:
    def test_verify_matching_identity(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sha = _init_git_repo(tmpdir, "https://github.com/owner/repo.git")
            identity = PRIdentity(owner="owner", repo="repo", head_sha=sha)
            ok, reason = _verify_local_identity(tmpdir, identity)
            assert ok is True
            assert reason == ""

    def test_verify_mismatched_remote(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sha = _init_git_repo(tmpdir, "https://github.com/other/repo.git")
            identity = PRIdentity(owner="owner", repo="repo", head_sha=sha)
            ok, reason = _verify_local_identity(tmpdir, identity)
            assert ok is False
            assert "Remote origin" in reason

    def test_verify_mismatched_sha(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _init_git_repo(tmpdir, "https://github.com/owner/repo.git")
            identity = PRIdentity(owner="owner", repo="repo", head_sha="deadbeef" * 5)
            ok, reason = _verify_local_identity(tmpdir, identity)
            assert ok is False
            assert "HEAD mismatch" in reason


class TestAskpassHelper:
    def test_create_and_cleanup_askpass(self):
        askpass_dir, script_path = _create_askpass_script("test-token")
        assert os.path.exists(script_path)
        _cleanup_askpass(askpass_dir)
        assert not os.path.exists(askpass_dir)


class TestRepoWorkspaceManager:
    def test_prepare_local_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sha = _init_git_repo(tmpdir, "https://github.com/owner/repo.git")
            identity = PRIdentity(owner="owner", repo="repo", head_sha=sha)
            manager = RepoWorkspaceManager()

            result = manager.prepare_workspace(
                run_id="run1",
                context_id="ctx1",
                pr_identity=identity,
                local_repo_root=tmpdir,
            )
            assert result.ok
            assert result.workspace is not None
            assert result.workspace.source == WorkspaceSource.LOCAL
            assert result.workspace.is_temp is False
            assert result.workspace.repo_root == tmpdir

    def test_prepare_local_workspace_mismatch_fails_without_temp_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _init_git_repo(tmpdir, "https://github.com/other/repo.git")
            identity = PRIdentity(owner="owner", repo="repo", head_sha="deadbeef" * 5)
            manager = RepoWorkspaceManager()

            result = manager.prepare_workspace(
                run_id="run1",
                context_id="ctx1",
                pr_identity=identity,
                local_repo_root=tmpdir,
            )
            assert result.ok is False
            assert result.error is not None
            assert result.error.kind == PreparationErrorKind.LOCAL_MISMATCH

    def test_missing_pr_identity_fails(self):
        manager = RepoWorkspaceManager()
        identity = PRIdentity(owner="", repo="repo", head_sha="abc")
        result = manager.prepare_workspace(
            run_id="run1",
            context_id="ctx1",
            pr_identity=identity,
        )
        assert result.ok is False
        assert result.error is not None
        assert result.error.kind == PreparationErrorKind.MISSING_PR_IDENTITY

    def test_get_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sha = _init_git_repo(tmpdir, "https://github.com/owner/repo.git")
            identity = PRIdentity(owner="owner", repo="repo", head_sha=sha)
            manager = RepoWorkspaceManager()
            manager.prepare_workspace("run1", "ctx1", identity, local_repo_root=tmpdir)

            ws = manager.get_workspace("run1", "ctx1")
            assert ws is not None
            assert ws.run_id == "run1"

            assert manager.get_workspace("run1", "nonexistent") is None

    def test_get_provider(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sha = _init_git_repo(tmpdir, "https://github.com/owner/repo.git")
            identity = PRIdentity(owner="owner", repo="repo", head_sha=sha)
            manager = RepoWorkspaceManager()
            manager.prepare_workspace("run1", "ctx1", identity, local_repo_root=tmpdir)

            provider = manager.get_provider("run1", "ctx1")
            assert provider is not None
            assert provider.repo_root == tmpdir

            assert manager.get_provider("run1", "nonexistent") is None

    def test_cleanup_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sha = _init_git_repo(tmpdir, "https://github.com/owner/repo.git")
            identity = PRIdentity(owner="owner", repo="repo", head_sha=sha)
            manager = RepoWorkspaceManager()
            manager.prepare_workspace("run1", "ctx1", identity, local_repo_root=tmpdir)

            assert manager.cleanup_workspace("run1", "ctx1") is True
            assert manager.get_workspace("run1", "ctx1") is None
            assert manager.cleanup_workspace("run1", "ctx1") is False

    def test_cleanup_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sha = _init_git_repo(tmpdir, "https://github.com/owner/repo.git")
            identity = PRIdentity(owner="owner", repo="repo", head_sha=sha)
            manager = RepoWorkspaceManager()
            manager.prepare_workspace("run1", "ctx1", identity, local_repo_root=tmpdir)
            manager.prepare_workspace("run1", "ctx2", identity, local_repo_root=tmpdir)

            count = manager.cleanup_run("run1")
            assert count == 2
            assert manager.get_workspace("run1", "ctx1") is None
            assert manager.get_workspace("run1", "ctx2") is None

    def test_cleanup_all(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sha = _init_git_repo(tmpdir, "https://github.com/owner/repo.git")
            identity = PRIdentity(owner="owner", repo="repo", head_sha=sha)
            manager = RepoWorkspaceManager()
            manager.prepare_workspace("run1", "ctx1", identity, local_repo_root=tmpdir)
            manager.prepare_workspace("run2", "ctx1", identity, local_repo_root=tmpdir)

            count = manager.cleanup_all()
            assert count == 2
            assert manager.get_workspace("run1", "ctx1") is None

    def test_cached_workspace_returned_on_second_call(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sha = _init_git_repo(tmpdir, "https://github.com/owner/repo.git")
            identity = PRIdentity(owner="owner", repo="repo", head_sha=sha)
            manager = RepoWorkspaceManager()

            r1 = manager.prepare_workspace("run1", "ctx1", identity, local_repo_root=tmpdir)
            r2 = manager.prepare_workspace("run1", "ctx1", identity, local_repo_root=tmpdir)
            assert r1.workspace is r2.workspace

    def test_safe_delete_refuses_outside_temp_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_root = os.path.join(tmpdir, "temp")
            os.makedirs(temp_root)
            manager = RepoWorkspaceManager(temp_root=temp_root)

            manager._safe_delete(tmpdir)

    def test_local_fallback_to_temp_clone_on_sha_mismatch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _init_git_repo(tmpdir, "https://github.com/owner/repo.git")
            temp_root = os.path.join(tmpdir, "temp_clones")
            os.makedirs(temp_root)
            identity = PRIdentity(owner="owner", repo="repo", head_sha="deadbeef" * 5)
            manager = RepoWorkspaceManager(temp_root=temp_root)

            result = manager.prepare_workspace(
                run_id="run1",
                context_id="ctx1",
                pr_identity=identity,
                local_repo_root=tmpdir,
            )
            assert result.ok is False
            assert result.error is not None
            assert result.error.kind == PreparationErrorKind.CLONE_FAILED
