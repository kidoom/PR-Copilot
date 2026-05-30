from __future__ import annotations

import os
import subprocess
import tempfile

import pytest

from backend.agent.model.client import ModelClient
from backend.agent.model.messages import Message, ModelResponse, ToolUseBlock
from backend.agent.tools.repo_context.provider.local import LocalRepoProvider
from backend.agent.tools.repo_context.provider.models import (
    PRIdentity,
    PreparationErrorKind,
    WorkspaceSource,
)
from backend.agent.tools.repo_context.provider.workspace import (
    RepoWorkspaceManager,
    _create_askpass_script,
    _cleanup_askpass,
)
from backend.deps import WorkspacePreparationError, create_agent_deps


def _init_git_repo(path: str, remote_url: str | None = None) -> str:
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


class FakeModel(ModelClient):
    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[list[Message]] = []

    async def chat(self, messages: list[Message], tool_schemas=None) -> ModelResponse:
        self.calls.append(list(messages))
        return self._responses.pop(0)


# --- Task 5.5: Integration test proving multiple subagents share one provider ---

class TestSharedProviderIntegration:
    @pytest.mark.asyncio
    async def test_multiple_subagents_share_provider(self):
        """Multiple subagents for the same run should share the same provider."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _init_git_repo(tmpdir, "https://github.com/owner/repo.git")
            with open(os.path.join(tmpdir, "src.py"), "w") as f:
                f.write("x = 1\n")
            subprocess.run(["git", "add", "."], cwd=tmpdir, capture_output=True, check=True)
            subprocess.run(["git", "commit", "-m", "add"], cwd=tmpdir, capture_output=True, check=True)
            result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmpdir, capture_output=True, text=True, check=True)
            sha = result.stdout.strip()

            identity = PRIdentity(owner="owner", repo="repo", head_sha=sha)
            manager = RepoWorkspaceManager()
            manager.prepare_workspace("run1", "ctx1", identity, local_repo_root=tmpdir)
            provider = manager.get_provider("run1", "ctx1")
            assert provider is not None

            deps = create_agent_deps()
            model = FakeModel([
                ModelResponse(content="done1", tool_use_blocks=[]),
                ModelResponse(content="done2", tool_use_blocks=[]),
            ])
            task_plan = {"context_id": "ctx1", "tasks": [], "routes": []}

            runtime = deps.build_main_runtime(
                model=model,
                task_plan=task_plan,
                pr_context=None,
                repo_root=tmpdir,
                run_id="run1",
            )

            result1 = await runtime.task_tool.run(
                prompt="inspect",
                agent_type="test-context-agent",
                task={"context_id": "ctx1", "task_id": "t1", "task_type": "test_context"},
            )
            result2 = await runtime.task_tool.run(
                prompt="inspect",
                agent_type="reference-context-agent",
                task={"context_id": "ctx1", "task_id": "t2", "task_type": "reference_context"},
            )

            bundle1 = runtime.child_bundles[result1.child_session_id]
            bundle2 = runtime.child_bundles[result2.child_session_id]

            assert bundle1.session.repo_root == bundle2.session.repo_root
            assert bundle1.session.context_id == bundle2.session.context_id
            assert bundle1.session.task_id != bundle2.session.task_id


# --- Task 6.1: Token safety tests ---

class TestTokenSafety:
    def test_askpass_script_cleanup(self):
        askpass_dir, script_path = _create_askpass_script("my-secret-token")
        assert os.path.exists(script_path)

        with open(script_path, "r") as f:
            content = f.read()
        assert "my-secret-token" in content

        _cleanup_askpass(askpass_dir)
        assert not os.path.exists(askpass_dir)
        assert not os.path.exists(script_path)

    def test_token_not_in_git_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sha = _init_git_repo(tmpdir, "https://github.com/owner/repo.git")
            git_config = os.path.join(tmpdir, ".git", "config")
            with open(git_config, "r") as f:
                content = f.read()
            assert "secret-token" not in content.lower()

    def test_workspace_repo_root_has_no_token(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sha = _init_git_repo(tmpdir, "https://github.com/owner/repo.git")
            identity = PRIdentity(owner="owner", repo="repo", head_sha=sha)
            manager = RepoWorkspaceManager()
            result = manager.prepare_workspace("run1", "ctx1", identity, local_repo_root=tmpdir)
            assert result.ok
            assert "token" not in result.workspace.repo_root.lower()


# --- Task 6.2: Missing PR identity and failed workspace preparation ---

class TestWorkspacePreparationBlocking:
    def test_missing_owner_blocks_dispatch(self):
        manager = RepoWorkspaceManager()
        identity = PRIdentity(owner="", repo="repo", head_sha="abc123")
        result = manager.prepare_workspace("run1", "ctx1", identity)
        assert result.ok is False
        assert result.error.kind == PreparationErrorKind.MISSING_PR_IDENTITY

    def test_missing_repo_blocks_dispatch(self):
        manager = RepoWorkspaceManager()
        identity = PRIdentity(owner="owner", repo="", head_sha="abc123")
        result = manager.prepare_workspace("run1", "ctx1", identity)
        assert result.ok is False
        assert result.error.kind == PreparationErrorKind.MISSING_PR_IDENTITY

    def test_missing_head_sha_blocks_dispatch(self):
        manager = RepoWorkspaceManager()
        identity = PRIdentity(owner="owner", repo="repo", head_sha="")
        result = manager.prepare_workspace("run1", "ctx1", identity)
        assert result.ok is False
        assert result.error.kind == PreparationErrorKind.MISSING_PR_IDENTITY

    def test_workspace_preparation_error_raises_on_build_runtime(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sha = _init_git_repo(tmpdir, "https://github.com/other/repo.git")
            identity = PRIdentity(owner="owner", repo="repo", head_sha="deadbeef" * 5)
            manager = RepoWorkspaceManager()
            deps = create_agent_deps()
            model = FakeModel([ModelResponse(content="done", tool_use_blocks=[])])

            with pytest.raises(WorkspacePreparationError):
                deps.build_main_runtime(
                    model=model,
                    task_plan={"context_id": "ctx1", "tasks": [], "routes": []},
                    pr_context=None,
                    repo_root=tmpdir,
                    run_id="run1",
                    workspace_manager=manager,
                    pr_identity=identity,
                )


# --- Task 6.3: Cleanup path safety ---

class TestCleanupPathSafety:
    def test_cleanup_refuses_outside_temp_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_root = os.path.join(tmpdir, "temp")
            os.makedirs(temp_root)
            manager = RepoWorkspaceManager(temp_root=temp_root)

            outside_path = os.path.join(tmpdir, "outside")
            os.makedirs(outside_path)

            manager._safe_delete(outside_path)
            assert os.path.exists(outside_path)

    def test_cleanup_deletes_inside_temp_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_root = os.path.join(tmpdir, "temp")
            os.makedirs(temp_root)
            inside_path = os.path.join(temp_root, "workspace")
            os.makedirs(inside_path)

            manager = RepoWorkspaceManager(temp_root=temp_root)
            manager._safe_delete(inside_path)
            assert not os.path.exists(inside_path)

    def test_cleanup_run_only_deletes_temp_workspaces(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sha = _init_git_repo(tmpdir, "https://github.com/owner/repo.git")
            identity = PRIdentity(owner="owner", repo="repo", head_sha=sha)
            manager = RepoWorkspaceManager()
            manager.prepare_workspace("run1", "ctx1", identity, local_repo_root=tmpdir)

            assert os.path.isdir(tmpdir)
            count = manager.cleanup_run("run1")
            assert count == 1
            assert os.path.isdir(tmpdir)
