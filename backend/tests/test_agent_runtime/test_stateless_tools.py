"""Tests for stateless repo context tools."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest
from unittest.mock import Mock, patch

from backend.agent.tools.repo_context.stateless_tools import (
    StatelessReadFilePatchTool,
    StatelessSearchDiffTool,
    StatelessSearchRepoTool,
    StatelessReadRepoFileTool,
    StatelessSearchTestsForTool,
    StatelessReadRepoManifestTool,
    StatelessVerifyRepoContextTool,
    StatelessTodoWriteTool,
    create_stateless_context_tools,
)
from backend.agent.tools.repo_context.models import (
    RepoContextSession,
    RepoVerificationState,
    TaskBudget,
    VerificationStatus,
)
from backend.agent.tools.repo_context.provider.models import PRIdentity


class MockPRFile:
    def __init__(self, filename, patch_available=True, hunks=None):
        self.filename = filename
        self.patch_available = patch_available
        self.hunks = hunks or []


class MockHunk:
    def __init__(self, header, lines):
        self.header = header
        self.lines = lines


class MockLine:
    def __init__(self, content, line_type="added", old_line=None, new_line=None):
        self.content = content
        self.type = line_type
        self.old_line = old_line
        self.new_line = new_line


class MockPRContext:
    def __init__(self, files):
        self.files = files
        self.owner = "owner"
        self.repo = "repo"
        self.commits = type("Commits", (), {"head_sha": ""})()


def _verified_session(repo_root: str, budget: TaskBudget | None = None) -> RepoContextSession:
    session = RepoContextSession(
        context_id="ctx",
        task_id="task",
        repo_root=repo_root,
        budget=budget or TaskBudget(),
    )
    session.verification = RepoVerificationState(
        status=VerificationStatus.VERIFIED,
        owner="owner",
        repo="repo",
    )
    return session


@pytest.fixture
def temp_repo():
    """Create a temporary repository for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create some test files
        os.makedirs(os.path.join(tmpdir, "src"))
        with open(os.path.join(tmpdir, "src", "main.py"), "w") as f:
            f.write("def main():\n    print('hello')\n")
        with open(os.path.join(tmpdir, "README.md"), "w") as f:
            f.write("# Test Project\n")
        with open(os.path.join(tmpdir, "package.json"), "w") as f:
            f.write('{"name": "test"}')
        yield tmpdir


@pytest.fixture
def pr_context():
    """Create a mock PR context."""
    return MockPRContext(files=[
        MockPRFile(
            filename="src/main.py",
            hunks=[
                MockHunk(
                    header="@@ -1,2 +1,3 @@",
                    lines=[
                        MockLine("def main():", "context", 1, 1),
                        MockLine("    print('hello')", "added", None, 2),
                        MockLine("    return True", "added", None, 3),
                    ],
                ),
            ],
        ),
        MockPRFile(filename="binary.png", patch_available=False),
    ])


class TestStatelessReadFilePatch:
    @pytest.mark.asyncio
    async def test_read_existing_file(self, pr_context):
        tool = StatelessReadFilePatchTool(pr_context)
        result = json.loads(await tool.call({"filename": "src/main.py"}))
        assert result["status"] == "ok"
        assert result["file"] == "src/main.py"
        assert len(result["hunks"]) == 1

    @pytest.mark.asyncio
    async def test_read_nonexistent_file(self, pr_context):
        tool = StatelessReadFilePatchTool(pr_context)
        result = json.loads(await tool.call({"filename": "nonexistent.py"}))
        assert result["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_read_unavailable_patch(self, pr_context):
        tool = StatelessReadFilePatchTool(pr_context)
        result = json.loads(await tool.call({"filename": "binary.png"}))
        assert result["status"] == "unavailable"


class TestStatelessSearchDiff:
    @pytest.mark.asyncio
    async def test_search_found(self, pr_context):
        tool = StatelessSearchDiffTool(pr_context)
        result = json.loads(await tool.call({"query": "hello"}))
        assert result["total"] > 0
        assert "hello" in result["matches"][0]["snippet"]

    @pytest.mark.asyncio
    async def test_search_not_found(self, pr_context):
        tool = StatelessSearchDiffTool(pr_context)
        result = json.loads(await tool.call({"query": "nonexistent"}))
        assert result["total"] == 0


class TestStatelessSearchRepo:
    @pytest.mark.asyncio
    async def test_search_found(self, temp_repo):
        tool = StatelessSearchRepoTool(temp_repo)
        result = json.loads(await tool.call({"query": "def main"}))
        assert result["total"] > 0
        assert "main.py" in result["matches"][0]["file"]

    @pytest.mark.asyncio
    async def test_search_with_path_scope(self, temp_repo):
        tool = StatelessSearchRepoTool(temp_repo)
        result = json.loads(await tool.call({"query": "def main", "path_scope": "src"}))
        assert result["total"] > 0

    @pytest.mark.asyncio
    async def test_search_ignores_sensitive_files(self, temp_repo):
        # Create a sensitive file
        with open(os.path.join(temp_repo, ".env"), "w") as f:
            f.write("SECRET=123")

        tool = StatelessSearchRepoTool(temp_repo)
        result = json.loads(await tool.call({"query": "SECRET"}))
        # Should not find results in .env
        env_matches = [m for m in result["matches"] if ".env" in m["file"]]
        assert len(env_matches) == 0

    @pytest.mark.asyncio
    async def test_search_budget_exhausted_with_session(self, temp_repo):
        session = _verified_session(temp_repo, TaskBudget(max_searches=1))
        tool = StatelessSearchRepoTool(temp_repo, session=session)
        first = json.loads(await tool.call({"query": "def main"}))
        second = json.loads(await tool.call({"query": "def main"}))
        assert "matches" in first
        assert "budget" in second["error"].lower()

    @pytest.mark.asyncio
    async def test_search_requires_verification_with_session(self, temp_repo):
        session = RepoContextSession(context_id="ctx", task_id="task", repo_root=temp_repo)
        tool = StatelessSearchRepoTool(temp_repo, session=session)
        result = json.loads(await tool.call({"query": "def main"}))
        assert "not verified" in result["error"].lower()


class TestStatelessReadRepoFile:
    @pytest.mark.asyncio
    async def test_read_existing_file(self, temp_repo):
        tool = StatelessReadRepoFileTool(temp_repo)
        result = json.loads(await tool.call({"path": "src/main.py"}))
        assert "lines" in result
        assert len(result["lines"]) > 0

    @pytest.mark.asyncio
    async def test_read_with_line_range(self, temp_repo):
        tool = StatelessReadRepoFileTool(temp_repo)
        result = json.loads(await tool.call({"path": "src/main.py", "start_line": 1, "max_lines": 1}))
        assert len(result["lines"]) == 1

    @pytest.mark.asyncio
    async def test_reject_path_traversal(self, temp_repo):
        tool = StatelessReadRepoFileTool(temp_repo)
        result = json.loads(await tool.call({"path": "../../../etc/passwd"}))
        assert "error" in result

    @pytest.mark.asyncio
    async def test_reject_sensitive_file(self, temp_repo):
        # Create a sensitive file
        with open(os.path.join(temp_repo, "private_key.pem"), "w") as f:
            f.write("key")

        tool = StatelessReadRepoFileTool(temp_repo)
        result = json.loads(await tool.call({"path": "private_key.pem"}))
        assert "error" in result

    @pytest.mark.asyncio
    async def test_read_file_budget_exhausted_with_session(self, temp_repo):
        session = _verified_session(temp_repo, TaskBudget(max_files=1))
        tool = StatelessReadRepoFileTool(temp_repo, session=session)
        first = json.loads(await tool.call({"path": "src/main.py"}))
        second = json.loads(await tool.call({"path": "src/main.py"}))
        assert "lines" in first
        assert "budget" in second["error"].lower()

    @pytest.mark.asyncio
    async def test_read_file_token_budget_exceeded_with_session(self, temp_repo):
        session = _verified_session(temp_repo, TaskBudget(max_tokens=1))
        tool = StatelessReadRepoFileTool(temp_repo, session=session)
        result = json.loads(await tool.call({"path": "src/main.py"}))
        assert "token budget" in result["error"].lower()


class TestStatelessSearchTestsFor:
    @pytest.mark.asyncio
    async def test_find_test_files(self, temp_repo):
        # Create test files
        with open(os.path.join(temp_repo, "test_main.py"), "w") as f:
            f.write("def test_main(): pass")

        tool = StatelessSearchTestsForTool(temp_repo)
        result = json.loads(await tool.call({"source_file": "src/main.py"}))
        assert result["total"] > 0
        assert "test_main.py" in result["candidates"][0]["file"]

    @pytest.mark.asyncio
    async def test_search_tests_budget_exhausted_with_session(self, temp_repo):
        session = _verified_session(temp_repo, TaskBudget(max_searches=1))
        tool = StatelessSearchTestsForTool(temp_repo, session=session)
        await tool.call({"source_file": "src/main.py"})
        result = json.loads(await tool.call({"source_file": "src/main.py"}))
        assert "budget" in result["error"].lower()


class TestStatelessReadRepoManifest:
    @pytest.mark.asyncio
    async def test_read_manifests(self, temp_repo):
        tool = StatelessReadRepoManifestTool(temp_repo)
        result = json.loads(await tool.call({}))
        assert "manifests" in result
        # Should find README.md and package.json
        assert "readme" in result["manifests"]
        assert "dependencies" in result["manifests"]

    @pytest.mark.asyncio
    async def test_manifest_uses_file_read_budget_with_session(self, temp_repo):
        session = _verified_session(temp_repo, TaskBudget(max_files=1))
        tool = StatelessReadRepoManifestTool(temp_repo, session=session)
        result = json.loads(await tool.call({}))
        assert "manifests" in result
        assert session.usage.file_read_count == 1
        second = json.loads(await tool.call({}))
        assert "budget" in second["error"].lower()


class TestStatelessVerifyRepoContext:
    @pytest.mark.asyncio
    async def test_verify_uses_bound_identity_over_model_input(self, temp_repo):
        os.makedirs(os.path.join(temp_repo, ".git"))
        session = RepoContextSession(context_id="ctx", task_id="task", repo_root=temp_repo)
        trusted = PRIdentity(owner="trusted-owner", repo="trusted-repo", head_sha="abc123")
        tool = StatelessVerifyRepoContextTool(
            temp_repo,
            session=session,
            trusted_identity=trusted,
        )

        def fake_run(args, **kwargs):
            if args[:3] == ["git", "remote", "get-url"]:
                return Mock(returncode=0, stdout="git@github.com:trusted-owner/trusted-repo.git")
            if args[:3] == ["git", "rev-parse", "HEAD"]:
                return Mock(returncode=0, stdout="abc123ffff")
            return Mock(returncode=1, stdout="")

        with patch("subprocess.run", side_effect=fake_run):
            result = json.loads(await tool.call({
                "owner": "attacker",
                "repo": "wrong-repo",
                "head_sha": "deadbeef",
            }))

        assert result["verified"] is True
        assert result["owner"] == "trusted-owner"
        assert result["repo"] == "trusted-repo"
        assert session.verification.status == VerificationStatus.VERIFIED

    @pytest.mark.asyncio
    async def test_verify_schema_does_not_require_identity_input(self, temp_repo):
        tool = StatelessVerifyRepoContextTool(temp_repo)
        assert "required" not in tool.input_schema


class TestStatelessTodoWrite:
    @pytest.mark.asyncio
    async def test_validate_todo_list(self):
        tool = StatelessTodoWriteTool()
        result = json.loads(await tool.call({
            "items": [
                {"content": "Task 1", "status": "in_progress"},
                {"content": "Task 2", "status": "pending"},
            ],
        }))
        assert result["updated"] is True
        assert result["items"] == 2

    @pytest.mark.asyncio
    async def test_reject_multiple_in_progress(self):
        tool = StatelessTodoWriteTool()
        result = json.loads(await tool.call({
            "items": [
                {"content": "Task 1", "status": "in_progress"},
                {"content": "Task 2", "status": "in_progress"},
            ],
        }))
        assert "error" in result


class TestCreateStatelessContextTools:
    def test_creates_all_tools(self, temp_repo, pr_context):
        tools = create_stateless_context_tools(temp_repo, pr_context)
        names = {t.name for t in tools}
        assert "read_file_patch" in names
        assert "search_diff" in names
        assert "search_repo" in names
        assert "read_repo_file" in names
        assert "search_tests_for" in names
        assert "read_repo_manifest" in names
        assert "verify_repo_context" in names
        assert "todo_write" in names

    def test_all_tools_read_only(self, temp_repo, pr_context):
        tools = create_stateless_context_tools(temp_repo, pr_context)
        for tool in tools:
            assert tool.is_read_only is True
