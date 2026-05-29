from __future__ import annotations

import json
from typing import Any

from backend.repo_context.models import (
    ContextEvidencePackage,
    ContextEvidenceRef,
    ContextFinding,
    PackageStatus,
    RepoContextSession,
    RepoVerificationState,
    VerificationStatus,
)
from backend.repo_context.policy import (
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


def verify_repo_context(
    session: RepoContextSession,
    owner: str,
    repo: str,
    head_sha: str = "",
    workspace_root: str = "",
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

    session.verification = RepoVerificationState(
        status=VerificationStatus.VERIFIED,
        owner=owner, repo=repo, head_sha=head_sha,
    )
    return {"verified": True, "owner": owner, "repo": repo, "head_sha": head_sha}


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
                    hunk_lines.append({"content": line.content, "type": line.line_type})
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
                        "line_number": line.line_number,
                        "line_type": line.line_type,
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
    query_lower = query.lower()
    matches = []
    max_results = min(limit, 50)
    root = session.repo_root

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not is_ignored_directory(d)]
        rel_dir = os.path.relpath(dirpath, root)
        if path_scope and not rel_dir.startswith(path_scope) and rel_dir != ".":
            continue
        for fname in filenames:
            fpath = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(fpath, root)
            if is_ignored_directory(rel_path):
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

    consume_file_read_budget(session)
    content = "\n".join(l["content"] for l in lines)
    consume_token_budget(session, _estimate_tokens(content))

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
