from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from backend.agent.tools.protocol import Tool, RiskLevel


# Constants for safety
IGNORED_DIRECTORIES = frozenset({
    ".git", "node_modules", "dist", "build", "coverage", ".venv", "venv",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".tox", ".eggs",
    "*.egg-info", ".next", ".nuxt", "target", "vendor",
})

SENSITIVE_FILE_PATTERNS = (
    ".env", ".env.", "private_key", "private-key", "id_rsa", "id_ed25519",
    ".pem", ".key", "credentials", "secret", ".secret",
)

MAX_LINES = 50
MAX_SEARCH_RESULTS = 50


def _is_ignored_directory(path: str) -> bool:
    """Check if path contains an ignored directory."""
    parts = Path(path).parts
    for part in parts:
        if part in IGNORED_DIRECTORIES:
            return True
        for pattern in IGNORED_DIRECTORIES:
            if pattern.startswith("*.") and part.endswith(pattern[1:]):
                return True
    return False


def _is_sensitive_file(path: str) -> bool:
    """Check if path is a sensitive file."""
    name = os.path.basename(path).lower()
    for pattern in SENSITIVE_FILE_PATTERNS:
        if pattern in name:
            return True
    return False


def _resolve_safe_path(repo_root: str, requested_path: str) -> str | None:
    """Resolve path safely, preventing traversal attacks."""
    root = Path(repo_root).resolve()
    target = (root / requested_path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return str(target)


class StatelessReadFilePatchTool(Tool):
    """Read diff/hunk patch for a file in the current PR."""

    def __init__(self, pr_context: Any) -> None:
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
        filename = input["filename"]

        for f in self._pr_context.files:
            if f.filename == filename:
                if not f.patch_available:
                    return json.dumps({"error": "Patch not available", "file": filename, "status": "unavailable"})
                hunks = []
                for h in f.hunks:
                    hunk_lines = []
                    for line in h.lines:
                        hunk_lines.append({"content": line.content, "type": line.type})
                    hunks.append({"header": h.header, "lines": hunk_lines})
                return json.dumps({"file": filename, "hunks": hunks, "status": "ok"})

        return json.dumps({"error": "File not found in PR", "file": filename, "status": "not_found"})


class StatelessSearchDiffTool(Tool):
    """Search within PR diff patches."""

    def __init__(self, pr_context: Any) -> None:
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
        query = input["query"].lower()
        limit = input.get("limit", 20)
        matches = []
        skipped = []

        for f in self._pr_context.files:
            if not f.patch_available:
                skipped.append(f.filename)
                continue
            for h_idx, h in enumerate(f.hunks):
                for line in h.lines:
                    if query in line.content.lower():
                        line_number = line.old_line if line.type == "removed" else line.new_line
                        matches.append({
                            "file": f.filename,
                            "hunk_index": h_idx,
                            "line_number": line_number,
                            "line_type": line.type,
                            "snippet": line.content.strip()[:200],
                        })
                        if len(matches) >= limit:
                            return json.dumps({"matches": matches, "total": len(matches), "skipped_files": skipped, "truncated": True})

        return json.dumps({"matches": matches, "total": len(matches), "skipped_files": skipped, "truncated": False})


class StatelessSearchRepoTool(Tool):
    """Search repository content by keyword."""

    def __init__(self, repo_root: str) -> None:
        self._repo_root = repo_root

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
        query = input["query"].lower()
        path_scope = input.get("path_scope", "")
        limit = min(input.get("limit", 20), MAX_SEARCH_RESULTS)
        matches = []

        root = Path(self._repo_root).resolve()

        # Resolve scope
        resolved_scope = None
        if path_scope:
            safe_scope = _resolve_safe_path(self._repo_root, path_scope)
            if not safe_scope:
                return json.dumps({"error": "Invalid or unsafe path_scope", "path_scope": path_scope})
            resolved_scope = Path(safe_scope).resolve()

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not _is_ignored_directory(d)]

            if resolved_scope:
                current_dir = Path(dirpath).resolve()
                try:
                    current_dir.relative_to(resolved_scope)
                except ValueError:
                    try:
                        resolved_scope.relative_to(current_dir)
                    except ValueError:
                        continue

            for fname in filenames:
                fpath = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(fpath, root)
                if _is_ignored_directory(rel_path):
                    continue
                if _is_sensitive_file(rel_path):
                    continue
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        for line_num, line in enumerate(f, 1):
                            if query in line.lower():
                                matches.append({
                                    "file": rel_path,
                                    "line": line_num,
                                    "snippet": line.strip()[:200],
                                })
                                if len(matches) >= limit:
                                    return json.dumps({"matches": matches, "total": len(matches), "truncated": True})
                except (OSError, UnicodeDecodeError):
                    continue

        return json.dumps({"matches": matches, "total": len(matches), "truncated": False})


class StatelessReadRepoFileTool(Tool):
    """Read a bounded snippet from a repository file."""

    def __init__(self, repo_root: str) -> None:
        self._repo_root = repo_root

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
        path = input["path"]
        start_line = input.get("start_line", 1)
        max_lines = min(input.get("max_lines", 50), MAX_LINES)

        # Safety checks
        safe_path = _resolve_safe_path(self._repo_root, path)
        if safe_path is None:
            return json.dumps({"error": "Path traversal rejected", "path": path})

        if _is_ignored_directory(path):
            return json.dumps({"error": "Path in ignored directory", "path": path})

        if _is_sensitive_file(path):
            return json.dumps({"error": "Sensitive file blocked", "path": path})

        if not os.path.isfile(safe_path):
            return json.dumps({"error": "File not found", "path": path})

        lines = []
        truncated = False
        try:
            with open(safe_path, "r", encoding="utf-8", errors="ignore") as f:
                for i, line in enumerate(f, 1):
                    if i < start_line:
                        continue
                    if i >= start_line + max_lines:
                        truncated = True
                        break
                    lines.append({"line": i, "content": line.rstrip("\n")})
        except OSError as e:
            return json.dumps({"error": str(e), "path": path})

        return json.dumps({
            "path": path,
            "start_line": start_line,
            "end_line": lines[-1]["line"] if lines else start_line,
            "lines": lines,
            "truncated": truncated,
        })


class StatelessSearchTestsForTool(Tool):
    """Find candidate test files related to a source file."""

    def __init__(self, repo_root: str) -> None:
        self._repo_root = repo_root

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
        source_file = input["source_file"]
        limit = input.get("limit", 20)

        root = self._repo_root
        basename = os.path.basename(source_file)
        name_no_ext = os.path.splitext(basename)[0]

        candidates = []
        patterns = [
            f"test_{name_no_ext}",
            f"{name_no_ext}_test",
            f"test-{name_no_ext}",
            f"{name_no_ext}.test",
            f"{name_no_ext}.spec",
        ]

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not _is_ignored_directory(d)]
            for fname in filenames:
                fname_lower = fname.lower()
                for pattern in patterns:
                    if pattern.lower() in fname_lower:
                        rel_path = os.path.relpath(os.path.join(dirpath, fname), root)
                        candidates.append({"file": rel_path, "reason": f"Matches pattern {pattern}"})
                        if len(candidates) >= limit:
                            return json.dumps({"candidates": candidates, "total": len(candidates), "status": "found"})

        status = "found" if candidates else "inconclusive"
        return json.dumps({"candidates": candidates, "total": len(candidates), "status": status})


class StatelessReadRepoManifestTool(Tool):
    """Read README, dependencies, CODEOWNERS, CI, and rule files."""

    _MANIFEST_FILES = {
        "readme": ["README.md", "README.rst", "README.txt", "README"],
        "dependencies": [
            "requirements.txt", "pyproject.toml", "setup.py", "setup.cfg",
            "package.json", "Cargo.toml", "go.mod", "Gemfile", "pom.xml", "build.gradle",
        ],
        "codeowners": ["CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"],
        "ci": [
            ".github/workflows", ".gitlab-ci.yml", "Jenkinsfile", ".circleci/config.yml",
        ],
        "rules": [
            "AGENTS.md", ".pr-copilot.yml", ".cursor/rules", ".windsurf/rules",
        ],
    }

    def __init__(self, repo_root: str) -> None:
        self._repo_root = repo_root

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
        root = self._repo_root
        manifests: dict[str, Any] = {}

        for category, paths in self._MANIFEST_FILES.items():
            found = []
            for p in paths:
                full = os.path.join(root, p)
                if os.path.isfile(full):
                    try:
                        with open(full, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read(2000)
                        found.append({"path": p, "content": content, "truncated": len(content) >= 2000})
                    except OSError:
                        pass
                elif os.path.isdir(full):
                    try:
                        entries = os.listdir(full)[:10]
                        found.append({"path": p, "entries": entries, "is_directory": True})
                    except OSError:
                        pass
            if found:
                manifests[category] = found

        return json.dumps({"manifests": manifests})


class StatelessVerifyRepoContextTool(Tool):
    """Verify repository workspace (diagnostic only, no state mutation)."""

    def __init__(self, repo_root: str, pr_context: Any = None) -> None:
        self._repo_root = repo_root
        self._pr_context = pr_context

    @property
    def name(self) -> str: return "verify_repo_context"
    @property
    def description(self) -> str: return "Verify repository workspace matches PR (diagnostic)"
    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"owner": {"type": "string"}, "repo": {"type": "string"}}, "required": ["owner", "repo"]}
    @property
    def risk_level(self) -> RiskLevel: return RiskLevel.LOW
    @property
    def is_read_only(self) -> bool: return True
    @property
    def is_concurrency_safe(self) -> bool: return True

    async def call(self, input: dict[str, Any]) -> str:
        import subprocess

        owner = input["owner"]
        repo = input["repo"]
        repo_root = self._repo_root

        if not os.path.isdir(repo_root):
            return json.dumps({"verified": False, "reason": "Repository root not found"})

        git_dir = os.path.join(repo_root, ".git")
        if not os.path.exists(git_dir):
            return json.dumps({"verified": False, "reason": "Not a git repository"})

        # Check remote URL
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=repo_root, capture_output=True, text=True, timeout=5,
            )
            remote = result.stdout.strip() if result.returncode == 0 else ""
            if f"{owner}/{repo}" not in remote.lower():
                return json.dumps({
                    "verified": False,
                    "reason": f"Remote origin does not match {owner}/{repo}",
                    "remote": remote,
                })
        except (OSError, subprocess.TimeoutExpired):
            return json.dumps({"verified": False, "reason": "Cannot read git remote"})

        # Check HEAD SHA
        head_sha = ""
        if self._pr_context:
            commits = getattr(self._pr_context, "commits", None)
            if commits:
                head_sha = getattr(commits, "head_sha", "")

        if head_sha:
            try:
                result = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=repo_root, capture_output=True, text=True, timeout=5,
                )
                actual_sha = result.stdout.strip() if result.returncode == 0 else ""
                if not actual_sha.startswith(head_sha[:12]):
                    return json.dumps({
                        "verified": False,
                        "reason": f"HEAD mismatch: expected {head_sha[:12]}, got {actual_sha[:12]}",
                    })
            except (OSError, subprocess.TimeoutExpired):
                return json.dumps({"verified": False, "reason": "Cannot read HEAD SHA"})

        return json.dumps({
            "verified": True,
            "owner": owner,
            "repo": repo,
            "repo_root": repo_root,
        })


class StatelessReadCheckSummaryTool(Tool):
    """Read CI/CD check summary (placeholder)."""

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
        return json.dumps({"status": "unavailable", "reason": "GitHub Checks integration not present"})


class StatelessTodoWriteTool(Tool):
    """Stateless todo checkpoint - validates and echoes without mutation."""

    @property
    def name(self) -> str: return "todo_write"
    @property
    def description(self) -> str: return "Create or update a task plan (stateless checkpoint)"
    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {"content": {"type": "string"}, "status": {"type": "string"}}}}}, "required": ["items"]}
    @property
    def risk_level(self) -> RiskLevel: return RiskLevel.LOW
    @property
    def is_read_only(self) -> bool: return True
    @property
    def is_concurrency_safe(self) -> bool: return True

    async def call(self, input: dict[str, Any]) -> str:
        items = input.get("items", [])

        # Validate: only one item can be in_progress
        in_progress = [i for i in items if i.get("status") == "in_progress"]
        if len(in_progress) > 1:
            return json.dumps({"error": "Only one item can be in_progress at a time"})

        # Stateless: just echo back the validated list
        return json.dumps({"updated": True, "items": len(items), "todo_list": items})


def create_stateless_context_tools(
    repo_root: str,
    pr_context: Any,
) -> list[Tool]:
    """Create stateless repo context tools.

    Args:
        repo_root: Path to the repository root. Must be a valid directory.
        pr_context: PR context for diff/patch operations.

    Returns:
        List of stateless tools.

    Raises:
        ValueError: If repo_root is empty or not a valid directory.
    """
    if not repo_root:
        raise ValueError("repo_root is required for stateless context tools")

    root_path = Path(repo_root).resolve()
    if not root_path.is_dir():
        raise ValueError(f"repo_root is not a valid directory: {repo_root}")

    return [
        StatelessTodoWriteTool(),
        StatelessVerifyRepoContextTool(repo_root, pr_context),
        StatelessReadFilePatchTool(pr_context),
        StatelessSearchDiffTool(pr_context),
        StatelessSearchRepoTool(repo_root),
        StatelessReadRepoFileTool(repo_root),
        StatelessSearchTestsForTool(repo_root),
        StatelessReadRepoManifestTool(repo_root),
        StatelessReadCheckSummaryTool(),
    ]
