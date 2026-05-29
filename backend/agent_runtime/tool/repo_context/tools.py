from __future__ import annotations

import json
from typing import Any

from backend.agent_runtime.tool.repo_context.models import (
    ContextEvidencePackage,
    ContextEvidenceRef,
    ContextFinding,
    PackageStatus,
    RepoContextSession,
    RepoVerificationState,
    VerificationStatus,
)
from backend.agent_runtime.tool.repo_context.policy import (
    check_budget_file_read,
    check_budget_search,
    check_budget_tokens,
    consume_file_read_budget,
    consume_search_budget,
    consume_token_budget,
    is_ignored_directory,
    is_sensitive_file,
    require_verification,
    resolve_safe_path,
)


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


# --- 3.1 verify_repo_context ---


def _get_git_remote_origin(repo_root: str) -> str | None:
    """Read the first remote origin URL from git config."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=repo_root, capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _get_git_head_sha(repo_root: str) -> str | None:
    """Read current HEAD commit SHA."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root, capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _parse_owner_repo_from_remote(remote_url: str) -> tuple[str, str] | None:
    """Extract owner/repo from a git remote URL."""
    import re
    # SSH: git@github.com:owner/repo.git
    m = re.search(r"[:/]([^/]+)/([^/]+?)(?:\.git)?$", remote_url)
    if m:
        return m.group(1), m.group(2)
    return None


def verify_repo_context(
    session: RepoContextSession,
    owner: str,
    repo: str,
    head_sha: str = "",
    workspace_root: str = "",
    pr_context: Any = None,
) -> dict[str, Any]:
    if workspace_root:
        session.repo_root = workspace_root
    if not session.repo_root:
        session.verification = RepoVerificationState(
            status=VerificationStatus.FAILED,
            reason="No workspace root provided",
        )
        return {"verified": False, "reason": "No workspace root provided"}

    import os
    git_dir = os.path.join(session.repo_root, ".git")
    if not os.path.exists(git_dir):
        session.verification = RepoVerificationState(
            status=VerificationStatus.FAILED,
            owner=owner, repo=repo,
            reason="Not a git repository",
        )
        return {"verified": False, "reason": "Not a git repository"}

    trusted_owner = owner
    trusted_repo = repo
    trusted_sha = head_sha
    if pr_context is not None:
        trusted_owner = getattr(pr_context, "owner", owner)
        trusted_repo = getattr(pr_context, "repo", repo)
        commits = getattr(pr_context, "commits", None)
        if commits is not None:
            trusted_sha = getattr(commits, "head_sha", head_sha)

    errors: list[str] = []

    remote_url = _get_git_remote_origin(session.repo_root)
    if remote_url:
        parsed = _parse_owner_repo_from_remote(remote_url)
        if parsed:
            remote_owner, remote_repo = parsed
            if remote_owner != trusted_owner or remote_repo != trusted_repo:
                errors.append(
                    f"Remote origin {remote_owner}/{remote_repo} does not match "
                    f"expected {trusted_owner}/{trusted_repo}"
                )

    if trusted_sha:
        actual_sha = _get_git_head_sha(session.repo_root)
        if actual_sha and not actual_sha.startswith(trusted_sha):
            errors.append(
                f"HEAD {actual_sha[:12]} does not match expected {trusted_sha[:12]}"
            )

    if errors:
        reason = "; ".join(errors)
        session.verification = RepoVerificationState(
            status=VerificationStatus.FAILED,
            owner=trusted_owner, repo=trusted_repo, head_sha=trusted_sha,
            reason=reason,
        )
        return {"verified": False, "reason": reason}

    session.verification = RepoVerificationState(
        status=VerificationStatus.VERIFIED,
        owner=trusted_owner, repo=trusted_repo, head_sha=trusted_sha,
    )
    return {"verified": True, "owner": trusted_owner, "repo": trusted_repo, "head_sha": trusted_sha}


# --- 3.2 read_file_patch ---


def read_file_patch(
    session: RepoContextSession,
    pr_context: Any,
    filename: str,
) -> dict[str, Any]:
    for f in pr_context.files:
        if f.filename == filename:
            if not f.patch_available:
                return {"error": "Patch not available", "file": filename, "status": "unavailable"}
            hunks = []
            for h in f.hunks:
                hunk_lines = []
                for line in h.lines:
                    hunk_lines.append({"content": line.content, "type": line.type})
                hunks.append({"header": h.header, "lines": hunk_lines})
            return {"file": filename, "hunks": hunks, "status": "ok"}
    return {"error": "File not found in PR", "file": filename, "status": "not_found"}


# --- 3.3 search_diff ---


def search_diff(
    session: RepoContextSession,
    pr_context: Any,
    query: str,
    limit: int = 20,
) -> dict[str, Any]:
    query_lower = query.lower()
    matches = []
    skipped = []
    for f in pr_context.files:
        if not f.patch_available:
            skipped.append(f.filename)
            continue
        for h_idx, h in enumerate(f.hunks):
            for line in h.lines:
                if query_lower in line.content.lower():
                    matches.append({
                        "file": f.filename,
                        "hunk_index": h_idx,
                        "line_number": line.new_line,
                        "line_type": line.type,
                        "snippet": line.content.strip()[:200],
                    })
                    if len(matches) >= limit:
                        return {"matches": matches, "total": len(matches), "skipped_files": skipped, "truncated": True}
    return {"matches": matches, "total": len(matches), "skipped_files": skipped, "truncated": False}


# --- 3.4 search_repo ---


def search_repo(
    session: RepoContextSession,
    query: str,
    path_scope: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    err = require_verification(session)
    if err:
        return {"error": err}
    if not check_budget_search(session):
        return {"error": "Search budget exhausted", "max_searches": session.budget.max_searches}

    import os
    from pathlib import Path
    query_lower = query.lower()
    matches = []
    max_results = min(limit, 50)
    root = session.repo_root
    root_path = Path(root).resolve()

    resolved_scope = None
    if path_scope:
        safe_scope = resolve_safe_path(root, path_scope)
        if safe_scope:
            resolved_scope = Path(safe_scope).resolve()

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not is_ignored_directory(d)]

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
            if is_ignored_directory(rel_path):
                continue
            if is_sensitive_file(rel_path):
                continue
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    for line_num, line in enumerate(f, 1):
                        if query_lower in line.lower():
                            matches.append({
                                "file": rel_path,
                                "line": line_num,
                                "snippet": line.strip()[:200],
                            })
                            if len(matches) >= max_results:
                                consume_search_budget(session)
                                return {"matches": matches, "total": len(matches), "truncated": True}
            except (OSError, UnicodeDecodeError):
                continue

    consume_search_budget(session)
    return {"matches": matches, "total": len(matches), "truncated": False}


# --- 3.5 read_repo_file ---


def read_repo_file(
    session: RepoContextSession,
    path: str,
    start_line: int = 1,
    max_lines: int = 50,
) -> dict[str, Any]:
    err = require_verification(session)
    if err:
        return {"error": err}
    if not check_budget_file_read(session):
        return {"error": "File read budget exhausted", "max_files": session.budget.max_files}

    safe_path = resolve_safe_path(session.repo_root, path)
    if safe_path is None:
        return {"error": "Path traversal rejected", "path": path}

    if is_ignored_directory(path):
        return {"error": "Path in ignored directory", "path": path}

    if is_sensitive_file(path):
        return {"error": "Sensitive file blocked", "path": path, "reason": "File matches sensitive pattern"}

    import os
    if not os.path.isfile(safe_path):
        return {"error": "File not found", "path": path}

    bounded_lines = min(max_lines, 50)
    lines = []
    truncated = False
    try:
        with open(safe_path, "r", encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f, 1):
                if i < start_line:
                    continue
                if i >= start_line + bounded_lines:
                    truncated = True
                    break
                lines.append({"line": i, "content": line.rstrip("\n")})
    except OSError as e:
        return {"error": str(e), "path": path}

    content = "\n".join(l["content"] for l in lines)
    estimated = _estimate_tokens(content)
    if not check_budget_tokens(session, estimated):
        return {
            "error": "Token budget exceeded",
            "path": path,
            "estimated_tokens": estimated,
            "remaining_tokens": max(0, session.budget.max_tokens - session.usage.approximate_tokens),
        }

    consume_file_read_budget(session)
    consume_token_budget(session, estimated)

    return {
        "path": path,
        "start_line": start_line,
        "end_line": lines[-1]["line"] if lines else start_line,
        "lines": lines,
        "truncated": truncated,
    }


# --- 3.6 search_tests_for ---


def search_tests_for(
    session: RepoContextSession,
    source_file: str,
    limit: int = 20,
) -> dict[str, Any]:
    err = require_verification(session)
    if err:
        return {"error": err}
    if not check_budget_search(session):
        return {"error": "Search budget exhausted"}

    import os
    root = session.repo_root
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
        dirnames[:] = [d for d in dirnames if not is_ignored_directory(d)]
        for fname in filenames:
            fname_lower = fname.lower()
            for pattern in patterns:
                if pattern.lower() in fname_lower:
                    rel_path = os.path.relpath(os.path.join(dirpath, fname), root)
                    candidates.append({"file": rel_path, "reason": f"Matches pattern {pattern}"})
                    if len(candidates) >= limit:
                        consume_search_budget(session)
                        return {"candidates": candidates, "total": len(candidates), "status": "found"}

    consume_search_budget(session)
    status = "found" if candidates else "inconclusive"
    return {"candidates": candidates, "total": len(candidates), "status": status}


# --- 3.7 read_repo_manifest ---


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


def read_repo_manifest(session: RepoContextSession) -> dict[str, Any]:
    err = require_verification(session)
    if err:
        return {"error": err}

    import os
    root = session.repo_root
    manifests: dict[str, Any] = {}

    for category, paths in _MANIFEST_FILES.items():
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

    return {"manifests": manifests}


# --- 3.8 read_check_summary ---


def read_check_summary(session: RepoContextSession) -> dict[str, Any]:
    return {"status": "unavailable", "reason": "GitHub Checks integration not present"}


# --- 3.9 finish_context_package ---


def finish_context_package(
    session: RepoContextSession,
    task_id: str,
    task_type: str,
    status: str,
    findings: list[dict[str, Any]],
    uncertainties: list[str] | None = None,
) -> dict[str, Any]:
    valid_statuses = {s.value for s in PackageStatus}
    if status not in valid_statuses:
        return {"error": f"Invalid status: {status}. Must be one of {valid_statuses}"}

    if not task_id:
        return {"error": "task_id is required"}
    if not task_type:
        return {"error": "task_type is required"}

    parsed_findings = []
    for f in findings:
        refs = []
        for ref in f.get("evidence", []):
            refs.append(ContextEvidenceRef(
                file=ref.get("file", ""),
                line=ref.get("line"),
                snippet=ref.get("snippet", "")[:500],
                source=ref.get("source", ""),
            ))
        parsed_findings.append(ContextFinding(
            claim=f.get("claim", ""),
            confidence=f.get("confidence", 0.5),
            evidence=refs,
        ))

    package = ContextEvidencePackage(
        task_id=task_id,
        task_type=task_type,
        status=PackageStatus(status),
        findings=parsed_findings,
        uncertainties=uncertainties or [],
        tool_usage=session.usage,
    )
    session.final_package = package

    return {
        "submitted": True,
        "task_id": task_id,
        "status": status,
        "findings_count": len(parsed_findings),
    }


# --- 3.10 todo_write ---


def todo_write(
    session: RepoContextSession,
    items: list[dict[str, str]],
) -> dict[str, Any]:
    in_progress = [i for i in items if i.get("status") == "in_progress"]
    if len(in_progress) > 1:
        return {"error": "Only one item can be in_progress at a time"}

    session.todos = items
    return {"updated": True, "items": len(items)}
