from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Any

from backend.agent.tools.repo_context.provider.helpers import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_FILE_LIST_RESULTS,
    DEFAULT_MAX_LINES,
    DEFAULT_MAX_SEARCH_RESULTS,
    is_ignored_directory,
    is_sensitive_file,
    normalize_repo_root,
    resolve_safe_path,
)
from backend.agent.tools.repo_context.provider.interface import (
    FileListResult,
    FileReadResult,
    FileEntry,
    ManifestEntry,
    ManifestResult,
    RepoProvider,
    SearchMatch,
    SearchResult,
)

_MANIFEST_FILES: dict[str, list[str]] = {
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


def _matches_glob(path: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(os.path.basename(path), pattern):
            return True
    return False


class LocalRepoProvider(RepoProvider):
    def __init__(self, repo_root: str) -> None:
        self._repo_root = normalize_repo_root(repo_root)

    @property
    def repo_root(self) -> str:
        return self._repo_root

    async def read_file(
        self,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
        max_bytes: int | None = None,
    ) -> FileReadResult:
        if is_ignored_directory(path):
            return FileReadResult(path=path, error="Path in ignored directory")
        if is_sensitive_file(path):
            return FileReadResult(path=path, error="Sensitive file blocked")

        safe = resolve_safe_path(self._repo_root, path)
        if safe is None:
            return FileReadResult(path=path, error="Path traversal rejected")
        if not os.path.isfile(safe):
            return FileReadResult(path=path, error="File not found")

        if end_line is None:
            end_line = start_line + DEFAULT_MAX_LINES - 1
        max_bytes = max_bytes or DEFAULT_MAX_BYTES

        lines: list[dict[str, Any]] = []
        truncated = False
        bytes_read = 0
        try:
            with open(safe, "r", encoding="utf-8", errors="ignore") as f:
                for i, line in enumerate(f, 1):
                    if i < start_line:
                        continue
                    if i > end_line:
                        truncated = True
                        break
                    content = line.rstrip("\n")
                    bytes_read += len(content.encode("utf-8"))
                    if bytes_read > max_bytes:
                        truncated = True
                        break
                    lines.append({"line": i, "content": content})
        except OSError as e:
            return FileReadResult(path=path, error=str(e))

        return FileReadResult(path=path, lines=lines, truncated=truncated)

    async def search_code(
        self,
        query: str,
        globs: list[str] | None = None,
        max_results: int = DEFAULT_MAX_SEARCH_RESULTS,
    ) -> SearchResult:
        max_results = min(max_results, DEFAULT_MAX_SEARCH_RESULTS)
        query_lower = query.lower()
        matches: list[SearchMatch] = []
        root = Path(self._repo_root)

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not is_ignored_directory(d)]
            for fname in filenames:
                fpath = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(fpath, root)
                if is_ignored_directory(rel_path) or is_sensitive_file(rel_path):
                    continue
                if globs and not _matches_glob(rel_path, globs):
                    continue
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        for line_num, line in enumerate(f, 1):
                            if query_lower in line.lower():
                                matches.append(SearchMatch(
                                    file=rel_path,
                                    line=line_num,
                                    snippet=line.strip()[:200],
                                ))
                                if len(matches) >= max_results:
                                    return SearchResult(matches=matches, truncated=True)
                except (OSError, UnicodeDecodeError):
                    continue

        return SearchResult(matches=matches, truncated=False)

    async def list_files(
        self,
        globs: list[str] | None = None,
        max_results: int = DEFAULT_MAX_FILE_LIST_RESULTS,
    ) -> FileListResult:
        entries: list[FileEntry] = []
        root = Path(self._repo_root)

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not is_ignored_directory(d)]
            for fname in filenames:
                fpath = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(fpath, root)
                if is_ignored_directory(rel_path) or is_sensitive_file(rel_path):
                    continue
                if globs and not _matches_glob(rel_path, globs):
                    continue
                entries.append(FileEntry(path=rel_path))
                if len(entries) >= max_results:
                    return FileListResult(entries=entries, truncated=True)

        return FileListResult(entries=entries, truncated=False)

    async def get_manifest(self) -> ManifestResult:
        manifests: dict[str, list[ManifestEntry]] = {}

        for category, paths in _MANIFEST_FILES.items():
            found: list[ManifestEntry] = []
            for p in paths:
                full = os.path.join(self._repo_root, p)
                if os.path.isfile(full):
                    try:
                        with open(full, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read(2000)
                        found.append(ManifestEntry(
                            path=p,
                            content=content,
                            truncated=len(content) >= 2000,
                        ))
                    except OSError:
                        pass
                elif os.path.isdir(full):
                    try:
                        dir_entries = os.listdir(full)[:10]
                        found.append(ManifestEntry(
                            path=p,
                            entries=dir_entries,
                            is_directory=True,
                        ))
                    except OSError:
                        pass
            if found:
                manifests[category] = found

        return ManifestResult(manifests=manifests)

    def verify(self) -> dict[str, Any]:
        if not os.path.isdir(self._repo_root):
            return {"verified": False, "reason": "Repository root not found"}
        git_dir = os.path.join(self._repo_root, ".git")
        if not os.path.exists(git_dir):
            return {"verified": False, "reason": "Not a git repository"}
        return {"verified": True, "repo_root": self._repo_root}
