from __future__ import annotations

import os
import subprocess
import tempfile
from unittest.mock import patch

import pytest

from backend.agent.tools.repo_context.provider.models import (
    PRIdentity,
    PreparationErrorKind,
    WorkspaceSource,
)
from backend.agent.tools.repo_context.provider.workspace import (
    RepoWorkspaceManager,
    _git_failure,
    _is_retryable_git_failure,
    _prepare_temp_clone,
    _sanitize_dir_name,
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


class TestTempClone:
    def test_relative_temp_root_uses_absolute_git_paths(self):
        with tempfile.TemporaryDirectory(dir=".") as tmpdir:
            relative_temp_root = os.path.relpath(tmpdir)
            identity = PRIdentity(owner="owner", repo="repo", head_sha="abc123def456")
            calls = []

            def fake_run_git(args, *, cwd, env=None, timeout=60):
                calls.append((args, cwd, env))
                if args[0] == "clone":
                    os.makedirs(args[-1])
                return subprocess.CompletedProcess(["git", *args], 0, "", "")

            with patch(
                "backend.agent.tools.repo_context.provider.workspace._run_git",
                side_effect=fake_run_git,
            ):
                clone_dir, error = _prepare_temp_clone(identity, relative_temp_root)

            assert error is None
            assert clone_dir is not None
            assert os.path.isabs(clone_dir)
            assert calls[0][1] == os.path.abspath(relative_temp_root)
            assert calls[0][0][-1] == clone_dir
            assert calls[0][2]["GIT_CONFIG_KEY_0"] == "credential.helper"
            assert calls[0][2]["GIT_CONFIG_VALUE_0"] == ""
            assert calls[1][1] == clone_dir

    def test_clone_failure_reports_stdout_and_exit_code(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = PRIdentity(owner="owner", repo="repo", head_sha="abc123def456")

            with patch(
                "backend.agent.tools.repo_context.provider.workspace._run_git",
                return_value=subprocess.CompletedProcess(
                    ["git", "clone"],
                    128,
                    "fatal: network unavailable",
                    "",
                ),
            ):
                clone_dir, error = _prepare_temp_clone(identity, tmpdir)

            assert clone_dir is None
            assert error == "Clone failed (exit 128): fatal: network unavailable"

    def test_fetch_retries_transient_network_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = PRIdentity(
                owner="owner",
                repo="repo",
                head_sha="abc123def456",
                pull_number=34,
            )
            fetch_attempts = 0

            def fake_run_git(args, *, cwd, env=None, timeout=60):
                nonlocal fetch_attempts
                if args[0] == "clone":
                    os.makedirs(args[-1])
                    return subprocess.CompletedProcess(["git", *args], 0, "", "")
                if args[0] == "fetch":
                    fetch_attempts += 1
                    if fetch_attempts == 1:
                        return subprocess.CompletedProcess(
                            ["git", *args],
                            128,
                            "",
                            "fatal: Recv failure: Connection was reset",
                        )
                return subprocess.CompletedProcess(["git", *args], 0, "", "")

            with (
                patch(
                    "backend.agent.tools.repo_context.provider.workspace._run_git",
                    side_effect=fake_run_git,
                ),
                patch("backend.agent.tools.repo_context.provider.workspace.time.sleep"),
            ):
                clone_dir, error = _prepare_temp_clone(identity, tmpdir)

            assert error is None
            assert clone_dir is not None
            assert fetch_attempts == 2

    def test_fetch_does_not_retry_missing_ref(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = PRIdentity(
                owner="owner",
                repo="repo",
                head_sha="abc123def456",
                pull_number=999,
            )
            fetch_attempts = 0

            def fake_run_git(args, *, cwd, env=None, timeout=60):
                nonlocal fetch_attempts
                if args[0] == "clone":
                    os.makedirs(args[-1])
                    return subprocess.CompletedProcess(["git", *args], 0, "", "")
                if args[0] == "fetch":
                    fetch_attempts += 1
                    return subprocess.CompletedProcess(
                        ["git", *args],
                        128,
                        "",
                        "fatal: couldn't find remote ref refs/pull/999/head",
                    )
                return subprocess.CompletedProcess(["git", *args], 0, "", "")

            with patch(
                "backend.agent.tools.repo_context.provider.workspace._run_git",
                side_effect=fake_run_git,
            ):
                clone_dir, error = _prepare_temp_clone(identity, tmpdir)

            assert clone_dir is None
            assert "couldn't find remote ref" in error
            assert fetch_attempts == 1

    def test_stale_identity_directory_does_not_block_fresh_clone(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = PRIdentity(owner="owner", repo="repo", head_sha="abc123def456")
            stale_dir = os.path.join(tmpdir, "owner__repo__abc123def456")
            os.makedirs(stale_dir)
            with open(os.path.join(stale_dir, "stale.txt"), "w") as f:
                f.write("stale")

            def fake_run_git(args, *, cwd, env=None, timeout=60):
                if args[0] == "clone":
                    os.makedirs(args[-1])
                return subprocess.CompletedProcess(["git", *args], 0, "", "")

            with patch(
                "backend.agent.tools.repo_context.provider.workspace._run_git",
                side_effect=fake_run_git,
            ):
                clone_dir, error = _prepare_temp_clone(identity, tmpdir)

            assert error is None
            assert clone_dir is not None
            assert clone_dir != stale_dir
            assert os.path.isdir(clone_dir)
            assert os.path.isfile(os.path.join(stale_dir, "stale.txt"))


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


class TestSanitizeDirName:
    def test_normal_name_unchanged(self):
        assert _sanitize_dir_name("my-repo") == "my-repo"

    def test_strips_invalid_chars(self):
        assert _sanitize_dir_name("org:repo") == "org_repo"
        assert _sanitize_dir_name("a<b>c") == "a_b_c"
        assert _sanitize_dir_name('a"b') == "a_b"

    def test_strips_trailing_dots_and_spaces(self):
        assert _sanitize_dir_name("repo...") == "repo"
        assert _sanitize_dir_name("repo   ") == "repo"

    def test_empty_returns_repo(self):
        assert _sanitize_dir_name("") == "repo"


def test_git_failure_reports_empty_output():
    result = subprocess.CompletedProcess(["git", "clone"], 1, "", "")
    assert _git_failure("Clone", result) == "Clone failed (exit 1): Git produced no output"


@pytest.mark.parametrize(
    "message",
    [
        "fatal: Recv failure: Connection was reset",
        "fatal: unable to access URL: Failed to connect",
        "fatal: schannel: failed to receive handshake",
        "fatal: early EOF",
    ],
)
def test_retryable_git_network_failures(message):
    result = subprocess.CompletedProcess(["git", "fetch"], 128, "", message)
    assert _is_retryable_git_failure(result) is True


def test_auth_failure_is_not_retryable():
    result = subprocess.CompletedProcess(
        ["git", "fetch"],
        128,
        "",
        "fatal: Authentication failed for repository",
    )
    assert _is_retryable_git_failure(result) is False
