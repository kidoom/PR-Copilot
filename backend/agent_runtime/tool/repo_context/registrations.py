from __future__ import annotations

from typing import Any

from backend.agent_runtime.tool.protocol import Tool, RiskLevel
from backend.agent_runtime.tool.repo_context.models import RepoContextSession
from backend.agent_runtime.tool.repo_context import tools as rc_tools


class VerifyRepoContextTool(Tool):
    def __init__(self, session: RepoContextSession, pr_context: Any = None) -> None:
        self._session = session
        self._pr_context = pr_context

    @property
    def name(self) -> str: return "verify_repo_context"
    @property
    def description(self) -> str: return "Verify repository workspace matches PR owner/repo/head_sha"
    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"owner": {"type": "string"}, "repo": {"type": "string"}, "head_sha": {"type": "string"}, "workspace_root": {"type": "string"}}, "required": ["owner", "repo"]}
    @property
    def risk_level(self) -> RiskLevel: return RiskLevel.LOW
    @property
    def is_read_only(self) -> bool: return True
    @property
    def is_concurrency_safe(self) -> bool: return False

    async def call(self, input: dict[str, Any]) -> str:
        import json
        result = rc_tools.verify_repo_context(self._session, input["owner"], input["repo"], input.get("head_sha", ""), input.get("workspace_root", ""), self._pr_context)
        return json.dumps(result)


class ReadFilePatchTool(Tool):
    def __init__(self, session: RepoContextSession, pr_context: Any) -> None:
        self._session = session
        self._pr_context = pr_context

    @property
    def name(self) -> str: return "read_file_patch"
    @property
    def description(self) -> str: return "Read diff/hunk patch for a file in the current PR"
    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"filename": {"type": "string"}}, "required": ["filename"]}
    @property
    def risk_level(self) -> RiskLevel: return RiskLevel.LOW
    @property
    def is_read_only(self) -> bool: return True
    @property
    def is_concurrency_safe(self) -> bool: return True

    async def call(self, input: dict[str, Any]) -> str:
        import json
        result = rc_tools.read_file_patch(self._session, self._pr_context, input["filename"])
        return json.dumps(result)


class SearchDiffTool(Tool):
    def __init__(self, session: RepoContextSession, pr_context: Any) -> None:
        self._session = session
        self._pr_context = pr_context

    @property
    def name(self) -> str: return "search_diff"
    @property
    def description(self) -> str: return "Search within PR diff patches"
    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]}
    @property
    def risk_level(self) -> RiskLevel: return RiskLevel.LOW
    @property
    def is_read_only(self) -> bool: return True
    @property
    def is_concurrency_safe(self) -> bool: return True

    async def call(self, input: dict[str, Any]) -> str:
        import json
        result = rc_tools.search_diff(self._session, self._pr_context, input["query"], input.get("limit", 20))
        return json.dumps(result)


class SearchRepoTool(Tool):
    def __init__(self, session: RepoContextSession) -> None:
        self._session = session

    @property
    def name(self) -> str: return "search_repo"
    @property
    def description(self) -> str: return "Search repository content by keyword"
    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"query": {"type": "string"}, "path_scope": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]}
    @property
    def risk_level(self) -> RiskLevel: return RiskLevel.LOW
    @property
    def is_read_only(self) -> bool: return True
    @property
    def is_concurrency_safe(self) -> bool: return True

    async def call(self, input: dict[str, Any]) -> str:
        import json
        result = rc_tools.search_repo(self._session, input["query"], input.get("path_scope", ""), input.get("limit", 20))
        return json.dumps(result)


class ReadRepoFileTool(Tool):
    def __init__(self, session: RepoContextSession) -> None:
        self._session = session

    @property
    def name(self) -> str: return "read_repo_file"
    @property
    def description(self) -> str: return "Read a bounded snippet from a repository file"
    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"}, "max_lines": {"type": "integer"}}, "required": ["path"]}
    @property
    def risk_level(self) -> RiskLevel: return RiskLevel.LOW
    @property
    def is_read_only(self) -> bool: return True
    @property
    def is_concurrency_safe(self) -> bool: return True

    async def call(self, input: dict[str, Any]) -> str:
        import json
        result = rc_tools.read_repo_file(self._session, input["path"], input.get("start_line", 1), input.get("max_lines", 50))
        return json.dumps(result)


class SearchTestsForTool(Tool):
    def __init__(self, session: RepoContextSession) -> None:
        self._session = session

    @property
    def name(self) -> str: return "search_tests_for"
    @property
    def description(self) -> str: return "Find candidate test files related to a source file"
    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"source_file": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["source_file"]}
    @property
    def risk_level(self) -> RiskLevel: return RiskLevel.LOW
    @property
    def is_read_only(self) -> bool: return True
    @property
    def is_concurrency_safe(self) -> bool: return True

    async def call(self, input: dict[str, Any]) -> str:
        import json
        result = rc_tools.search_tests_for(self._session, input["source_file"], input.get("limit", 20))
        return json.dumps(result)


class ReadRepoManifestTool(Tool):
    def __init__(self, session: RepoContextSession) -> None:
        self._session = session

    @property
    def name(self) -> str: return "read_repo_manifest"
    @property
    def description(self) -> str: return "Read README, dependencies, CODEOWNERS, CI, and rule files"
    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}
    @property
    def risk_level(self) -> RiskLevel: return RiskLevel.LOW
    @property
    def is_read_only(self) -> bool: return True
    @property
    def is_concurrency_safe(self) -> bool: return True

    async def call(self, input: dict[str, Any]) -> str:
        import json
        result = rc_tools.read_repo_manifest(self._session)
        return json.dumps(result)


class ReadCheckSummaryTool(Tool):
    def __init__(self, session: RepoContextSession) -> None:
        self._session = session

    @property
    def name(self) -> str: return "read_check_summary"
    @property
    def description(self) -> str: return "Read CI/CD check summary (placeholder)"
    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}
    @property
    def risk_level(self) -> RiskLevel: return RiskLevel.LOW
    @property
    def is_read_only(self) -> bool: return True
    @property
    def is_concurrency_safe(self) -> bool: return True

    async def call(self, input: dict[str, Any]) -> str:
        import json
        result = rc_tools.read_check_summary(self._session)
        return json.dumps(result)


class FinishContextPackageTool(Tool):
    def __init__(self, session: RepoContextSession) -> None:
        self._session = session

    @property
    def name(self) -> str: return "finish_context_package"
    @property
    def description(self) -> str: return "Submit structured ContextEvidencePackage as final task output"
    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"task_id": {"type": "string"}, "task_type": {"type": "string"}, "status": {"type": "string", "enum": ["found_context", "inconclusive", "blocked", "error"]}, "findings": {"type": "array", "items": {"type": "object", "properties": {"claim": {"type": "string"}, "confidence": {"type": "number"}, "evidence": {"type": "array"}}}}, "uncertainties": {"type": "array", "items": {"type": "string"}}}, "required": ["task_id", "task_type", "status", "findings"]}
    @property
    def risk_level(self) -> RiskLevel: return RiskLevel.LOW
    @property
    def is_read_only(self) -> bool: return True
    @property
    def is_concurrency_safe(self) -> bool: return False

    async def call(self, input: dict[str, Any]) -> str:
        import json
        result = rc_tools.finish_context_package(self._session, input["task_id"], input["task_type"], input["status"], input["findings"], input.get("uncertainties"))
        return json.dumps(result)


class TodoWriteTool(Tool):
    def __init__(self, session: RepoContextSession) -> None:
        self._session = session

    @property
    def name(self) -> str: return "todo_write"
    @property
    def description(self) -> str: return "Create or update a task plan for the current context session"
    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {"content": {"type": "string"}, "status": {"type": "string"}}}}}, "required": ["items"]}
    @property
    def risk_level(self) -> RiskLevel: return RiskLevel.LOW
    @property
    def is_read_only(self) -> bool: return True
    @property
    def is_concurrency_safe(self) -> bool: return False

    async def call(self, input: dict[str, Any]) -> str:
        import json
        result = rc_tools.todo_write(self._session, input["items"])
        return json.dumps(result)


TOOL_NAME_SET = frozenset({
    "todo_write", "verify_repo_context", "read_file_patch", "search_diff",
    "search_repo", "read_repo_file", "search_tests_for", "read_repo_manifest",
    "read_check_summary", "finish_context_package",
})


def create_context_tools(
    session: RepoContextSession,
    pr_context: Any,
) -> list[Tool]:
    return [
        TodoWriteTool(session),
        VerifyRepoContextTool(session, pr_context),
        ReadFilePatchTool(session, pr_context),
        SearchDiffTool(session, pr_context),
        SearchRepoTool(session),
        ReadRepoFileTool(session),
        SearchTestsForTool(session),
        ReadRepoManifestTool(session),
        ReadCheckSummaryTool(session),
        FinishContextPackageTool(session),
    ]
