from __future__ import annotations

import json
import os
import tempfile
import pytest
from typing import Any

from backend.agent.tools.repo_context.models import (
    ContextEvidencePackage,
    ContextEvidenceRef,
    ContextFinding,
    IGNORED_DIRECTORIES,
    PackageStatus,
    RepoContextSession,
    RepoVerificationState,
    TaskBudget,
    ToolUsage,
    VerificationStatus,
)
from backend.agent.tools.repo_context.policy import (
    check_budget_file_read,
    check_budget_search,
    check_budget_tokens,
    consume_file_read_budget,
    consume_search_budget,
    consume_token_budget,
    is_ignored_directory,
    is_sensitive_file,
    is_verified,
    require_verification,
    resolve_safe_path,
)
from backend.agent.tools.repo_context.service import (
    finish_context_package,
    read_check_summary,
    read_file_patch,
    read_repo_file,
    read_repo_manifest,
    search_diff,
    search_repo,
    search_tests_for,
    todo_write,
    verify_repo_context,
)
from backend.agent.tools.repo_context.tool_defs import (
    create_context_tools,
    TOOL_NAME_SET,
)


# --- Helpers ---


def _make_session(**overrides) -> RepoContextSession:
    defaults = dict(context_id="ctx_test", task_id="task_test", repo_root="", budget=TaskBudget())
    defaults.update(overrides)
    return RepoContextSession(**defaults)


def _make_verified_session(repo_root: str = "", **overrides) -> RepoContextSession:
    session = _make_session(repo_root=repo_root, **overrides)
    session.verification = RepoVerificationState(
        status=VerificationStatus.VERIFIED, owner="test", repo="repo", head_sha="abc123"
    )
    return session


# --- Model tests ---


def test_repo_context_session_defaults():
    s = RepoContextSession(context_id="ctx1", task_id="t1")
    assert s.verification.status == VerificationStatus.UNVERIFIED
    assert s.budget.max_searches == 5
    assert s.usage.search_count == 0
    assert s.final_package is None


def test_verification_state():
    v = RepoVerificationState(status=VerificationStatus.VERIFIED, owner="o", repo="r")
    assert v.status == VerificationStatus.VERIFIED
    assert v.owner == "o"


def test_tool_usage():
    u = ToolUsage()
    assert u.search_count == 0
    assert u.file_read_count == 0
    assert u.approximate_tokens == 0


def test_context_finding():
    f = ContextFinding(claim="SQL injection risk", confidence=0.9, evidence=[ContextEvidenceRef(file="src/auth.py", line=42)])
    assert f.claim == "SQL injection risk"
    assert len(f.evidence) == 1


def test_context_evidence_package():
    p = ContextEvidencePackage(task_id="t1", task_type="security_context", status=PackageStatus.FOUND_CONTEXT)
    assert p.status == PackageStatus.FOUND_CONTEXT
    assert p.findings == []


# --- Policy tests ---


def test_resolve_safe_path_valid():
    with tempfile.TemporaryDirectory() as d:
        result = resolve_safe_path(d, "src/main.py")
        assert result is not None
        assert os.path.basename(result) == "main.py"


def test_resolve_safe_path_traversal():
    with tempfile.TemporaryDirectory() as d:
        result = resolve_safe_path(d, "../../../etc/passwd")
        assert result is None


def test_resolve_safe_path_absolute_outside():
    with tempfile.TemporaryDirectory() as d:
        result = resolve_safe_path(d, "/etc/passwd")
        assert result is None


def test_is_ignored_directory():
    assert is_ignored_directory("node_modules/package") is True
    assert is_ignored_directory(".git/objects") is True
    assert is_ignored_directory("__pycache__/mod.pyc") is True
    assert is_ignored_directory("src/main.py") is False


def test_is_sensitive_file():
    assert is_sensitive_file(".env") is True
    assert is_sensitive_file(".env.local") is True
    assert is_sensitive_file("id_rsa") is True
    assert is_sensitive_file("private_key.pem") is True
    assert is_sensitive_file("src/main.py") is False


def test_budget_tracking():
    s = _make_session()
    assert check_budget_search(s) is True
    consume_search_budget(s)
    assert s.usage.search_count == 1
    assert check_budget_file_read(s) is True
    consume_file_read_budget(s)
    assert s.usage.file_read_count == 1


def test_budget_exhaustion():
    s = _make_session(budget=TaskBudget(max_searches=1, max_files=1, max_tokens=100))
    consume_search_budget(s)
    assert check_budget_search(s) is False
    consume_file_read_budget(s)
    assert check_budget_file_read(s) is False
    consume_token_budget(s, 50)
    assert check_budget_tokens(s, 60) is False


def test_verification_gating():
    s = _make_session()
    assert is_verified(s) is False
    assert require_verification(s) is not None


def test_verification_passes():
    s = _make_verified_session()
    assert is_verified(s) is True
    assert require_verification(s) is None


# --- Tool tests ---


def test_verify_repo_context_success():
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, ".git"))
        s = _make_session()
        with patch("backend.agent.tools.repo_context.service._get_git_remote_origin", return_value="git@github.com:owner/repo.git"), \
             patch("backend.agent.tools.repo_context.service._get_git_head_sha", return_value="sha123abc"):
            result = verify_repo_context(s, "owner", "repo", "sha123", d)
        assert result["verified"] is True
        assert s.verification.status == VerificationStatus.VERIFIED


def test_verify_repo_context_fails_when_remote_unreadable():
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, ".git"))
        s = _make_session()
        with patch("backend.agent.tools.repo_context.service._get_git_remote_origin", return_value=None):
            result = verify_repo_context(s, "owner", "repo", "sha123", d)
        assert result["verified"] is False
        assert "remote origin" in result["reason"].lower()


def test_verify_repo_context_fails_when_head_unreadable():
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, ".git"))
        s = _make_session()
        with patch("backend.agent.tools.repo_context.service._get_git_remote_origin", return_value="git@github.com:owner/repo.git"), \
             patch("backend.agent.tools.repo_context.service._get_git_head_sha", return_value=None):
            result = verify_repo_context(s, "owner", "repo", "sha123", d)
        assert result["verified"] is False
        assert "head" in result["reason"].lower()


def test_verify_repo_context_no_git():
    with tempfile.TemporaryDirectory() as d:
        s = _make_session()
        result = verify_repo_context(s, "owner", "repo", "", d)
        assert result["verified"] is False
        assert "Not a git" in result["reason"]


def test_search_repo_requires_verification():
    s = _make_session()
    result = search_repo(s, "test")
    assert "error" in result
    assert "not verified" in result["error"].lower()


def test_search_repo_budget_exhausted():
    s = _make_verified_session(budget=TaskBudget(max_searches=0))
    result = search_repo(s, "test")
    assert "error" in result
    assert "budget" in result["error"].lower()


def test_read_repo_file_requires_verification():
    s = _make_session()
    result = read_repo_file(s, "src/main.py")
    assert "error" in result


def test_read_repo_file_traversal_rejected():
    s = _make_verified_session()
    result = read_repo_file(s, "../../../etc/passwd")
    assert "error" in result


def test_read_repo_file_sensitive_blocked():
    with tempfile.TemporaryDirectory() as d:
        s = _make_verified_session(repo_root=d)
        result = read_repo_file(s, ".env")
        assert "error" in result
        assert "sensitive" in result["error"].lower()


def test_read_repo_file_not_found():
    with tempfile.TemporaryDirectory() as d:
        s = _make_verified_session(repo_root=d)
        result = read_repo_file(s, "nonexistent.py")
        assert "error" in result
        assert "not found" in result["error"].lower()


def test_read_repo_file_success():
    with tempfile.TemporaryDirectory() as d:
        fpath = os.path.join(d, "test.py")
        with open(fpath, "w") as f:
            f.write("line 1\nline 2\nline 3\n")
        s = _make_verified_session(repo_root=d)
        result = read_repo_file(s, "test.py")
        assert "lines" in result
        assert len(result["lines"]) == 3


def test_read_check_summary():
    s = _make_session()
    result = read_check_summary(s)
    assert result["status"] == "unavailable"


def test_finish_context_package_success():
    s = _make_session()
    result = finish_context_package(s, "t1", "security_context", "found_context", [{"claim": "test", "confidence": 0.9}])
    assert result["submitted"] is True
    assert s.final_package is not None
    assert s.final_package.status == PackageStatus.FOUND_CONTEXT


def test_finish_context_package_invalid_status():
    s = _make_session()
    result = finish_context_package(s, "t1", "security_context", "invalid_status", [])
    assert "error" in result


def test_finish_context_package_missing_task_id():
    s = _make_session()
    result = finish_context_package(s, "", "security_context", "found_context", [])
    assert "error" in result


def test_todo_write_success():
    s = _make_session()
    result = todo_write(s, [{"content": "step 1", "status": "in_progress"}, {"content": "step 2", "status": "pending"}])
    assert result["updated"] is True


def test_todo_write_rejects_multiple_in_progress():
    s = _make_session()
    result = todo_write(s, [{"content": "a", "status": "in_progress"}, {"content": "b", "status": "in_progress"}])
    assert "error" in result


# --- Registration tests ---


def test_create_context_tools_returns_all():
    s = _make_session()
    tools = create_context_tools(s, pr_context=None)
    names = {t.name for t in tools}
    assert names == TOOL_NAME_SET


def test_tool_schemas_are_model_safe():
    s = _make_session()
    tools = create_context_tools(s, pr_context=None)
    for t in tools:
        schema = t.input_schema
        assert "type" in schema
        assert not hasattr(schema, "risk_level")


def test_all_tools_read_only():
    s = _make_session()
    tools = create_context_tools(s, pr_context=None)
    for t in tools:
        assert t.is_read_only is True


def test_patch_deep_dive_excludes_search_repo():
    from backend.domain.review.context_task_planner import TASK_ROUTES
    route = TASK_ROUTES["patch_deep_dive"]
    assert "search_repo" not in route.allowed_tools
    assert "read_file_patch" in route.allowed_tools
    assert "finish_context_package" in route.allowed_tools


def test_all_routes_include_todo_write_and_finish():
    from backend.domain.review.context_task_planner import TASK_ROUTES
    for tt, route in TASK_ROUTES.items():
        assert "todo_write" in route.allowed_tools, f"{tt} missing todo_write"
        assert "finish_context_package" in route.allowed_tools, f"{tt} missing finish_context_package"


def test_agent_definitions_deny_recursive_tools():
    from backend.domain.review.context_task_planner import AGENT_DEFINITIONS
    for name, agent in AGENT_DEFINITIONS.items():
        assert "task_tool" in agent.disallowed_tools, f"{name} missing task_tool deny"
        assert "sub_agent" in agent.disallowed_tools, f"{name} missing sub_agent deny"
