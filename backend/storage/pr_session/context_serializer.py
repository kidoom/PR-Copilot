"""Versioned serializer and deserializer for PRContext.

Converts between in-memory ``PRContext`` objects and the durable
``ContextRecord`` model used by the PR session store.  Hunks and patch
content are bounded by a configurable maximum line count to prevent
unbounded storage growth.
"""

from __future__ import annotations

from typing import Any

from backend.domain.pr_context.context_manager import (
    DerivedSignals,
    FileEntry,
    PRContext,
)
from backend.domain.pr_context.fetcher import CommitInfo, CommitsData, PRMetadata
from backend.domain.pr_context.hunk_parser import Hunk, HunkLine
from backend.storage.pr_session.models import ContextRecord

# Maximum total hunk lines to persist per file.
DEFAULT_MAX_HUNK_LINES_PER_FILE = 200

# Maximum total patch content size in characters (approx 200 KB).
DEFAULT_MAX_PATCH_CONTENT_CHARS = 200_000


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _serialize_hunk_line(line: HunkLine) -> dict[str, Any]:
    return {
        "type": line.type,
        "content": line.content,
        "old_line": line.old_line,
        "new_line": line.new_line,
    }


def _serialize_hunk(hunk: Hunk) -> dict[str, Any]:
    return {
        "header": hunk.header,
        "old_start": hunk.old_start,
        "old_lines": hunk.old_lines,
        "new_start": hunk.new_start,
        "new_lines": hunk.new_lines,
        "lines": [_serialize_hunk_line(l) for l in hunk.lines],
    }


def _serialize_file_entry(
    entry: FileEntry,
    *,
    max_hunk_lines: int = DEFAULT_MAX_HUNK_LINES_PER_FILE,
    include_hunks: bool = True,
) -> dict[str, Any]:
    d: dict[str, Any] = {
        "filename": entry.filename,
        "status": entry.status,
        "additions": entry.additions,
        "deletions": entry.deletions,
        "changes": entry.changes,
        "language": entry.language,
        "language_family": entry.language_family,
        "rule_profile": entry.rule_profile,
        "is_test": entry.is_test,
        "is_docs": entry.is_docs,
        "is_config": entry.is_config,
        "is_source": entry.is_source,
        "is_generated": entry.is_generated,
        "is_binary": entry.is_binary,
        "patch_available": entry.patch_available,
        "large_patch": entry.large_patch,
        "is_high_risk_path": entry.is_high_risk_path,
        "risk_hints": entry.risk_hints,
        "priority_score_hint": entry.priority_score_hint,
        "hunk_count": entry.hunk_count,
        "added_line_count": entry.added_line_count,
        "removed_line_count": entry.removed_line_count,
        "keywords": entry.keywords,
    }
    if entry.previous_filename:
        d["previous_filename"] = entry.previous_filename
    if entry.parse_error:
        d["parse_error"] = entry.parse_error

    if include_hunks and entry.hunks:
        # Bound hunk lines
        total_lines = 0
        bounded_hunks = []
        for hunk in entry.hunks:
            remaining = max_hunk_lines - total_lines
            if remaining <= 0:
                break
            if len(hunk.lines) > remaining:
                bounded = Hunk(
                    header=hunk.header,
                    old_start=hunk.old_start,
                    old_lines=hunk.old_lines,
                    new_start=hunk.new_start,
                    new_lines=hunk.new_lines,
                    lines=hunk.lines[:remaining],
                )
                bounded_hunks.append(_serialize_hunk(bounded))
                total_lines += remaining
            else:
                bounded_hunks.append(_serialize_hunk(hunk))
                total_lines += len(hunk.lines)
        d["hunks"] = bounded_hunks

    return d


def _serialize_pr_metadata(pr: PRMetadata) -> dict[str, Any]:
    return {
        "title": pr.title,
        "body": pr.body[:2000] if pr.body else "",  # Bound body
        "author": pr.author,
        "url": pr.url,
        "state": pr.state,
        "merged": pr.merged,
        "base_branch": pr.base_branch,
        "head_branch": pr.head_branch,
        "created_at": pr.created_at,
        "updated_at": pr.updated_at,
        "additions": pr.additions,
        "deletions": pr.deletions,
        "changed_files": pr.changed_files,
        "labels": pr.labels,
        "assignees": pr.assignees,
        "requested_reviewers": pr.requested_reviewers,
    }


def _serialize_commits(commits: CommitsData) -> dict[str, Any]:
    return {
        "head_sha": commits.head_sha,
        "commits": [
            {
                "sha": c.sha,
                "message": c.message[:500] if c.message else "",  # Bound message
                "author": c.author,
                "date": c.date,
            }
            for c in commits.commits
        ],
    }


def _serialize_derived(derived: DerivedSignals | None) -> dict[str, Any]:
    if derived is None:
        return {}
    return {
        "total_hunks": derived.total_hunks,
        "source_files_changed": derived.source_files_changed,
        "test_files_changed": derived.test_files_changed,
        "docs_only": derived.docs_only,
        "has_source_without_tests": derived.has_source_without_tests,
        "high_risk_files": derived.high_risk_files,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def serialize_context(
    ctx: PRContext,
    pr_session_id: str,
    *,
    max_hunk_lines: int = DEFAULT_MAX_HUNK_LINES_PER_FILE,
) -> ContextRecord:
    """Convert an in-memory ``PRContext`` to a durable ``ContextRecord``.

    Hunks and patch content are bounded to prevent unbounded storage.
    """
    files_data = [
        _serialize_file_entry(f, max_hunk_lines=max_hunk_lines)
        for f in ctx.files
    ]

    # Compute total patch content chars for logging / size boundary tests
    patch_content: dict[str, Any] = {
        "file_count": len(files_data),
        "included_hunks": True,
    }

    return ContextRecord(
        context_id=ctx.context_id,
        pr_session_id=pr_session_id,
        owner=ctx.owner,
        repo=ctx.repo,
        pull_number=ctx.pull_number,
        base_sha=ctx.commits.commits[0].sha if ctx.commits.commits else "",
        head_sha=ctx.commits.head_sha,
        pr_metadata=_serialize_pr_metadata(ctx.pr),
        commits=_serialize_commits(ctx.commits),
        files=files_data,
        derived=_serialize_derived(ctx.derived),
        patch_content=patch_content,
    )


# ---------------------------------------------------------------------------
# Deserialization
# ---------------------------------------------------------------------------


def _deserialize_hunk_line(data: dict[str, Any]) -> HunkLine:
    return HunkLine(
        type=data["type"],
        content=data["content"],
        old_line=data.get("old_line"),
        new_line=data.get("new_line"),
    )


def _deserialize_hunk(data: dict[str, Any]) -> Hunk:
    return Hunk(
        header=data["header"],
        old_start=data["old_start"],
        old_lines=data["old_lines"],
        new_start=data["new_start"],
        new_lines=data["new_lines"],
        lines=[_deserialize_hunk_line(l) for l in data.get("lines", [])],
    )


def _deserialize_file_entry(data: dict[str, Any]) -> FileEntry:
    return FileEntry(
        filename=data["filename"],
        previous_filename=data.get("previous_filename"),
        status=data["status"],
        additions=data["additions"],
        deletions=data["deletions"],
        changes=data["changes"],
        language=data["language"],
        language_family=data["language_family"],
        rule_profile=data["rule_profile"],
        is_test=data["is_test"],
        is_docs=data["is_docs"],
        is_config=data["is_config"],
        is_source=data["is_source"],
        is_generated=data["is_generated"],
        is_binary=data["is_binary"],
        patch_available=data["patch_available"],
        large_patch=data.get("large_patch", False),
        parse_error=data.get("parse_error"),
        is_high_risk_path=data["is_high_risk_path"],
        risk_hints=data.get("risk_hints", []),
        priority_score_hint=data.get("priority_score_hint", 0),
        hunk_count=data.get("hunk_count", 0),
        added_line_count=data.get("added_line_count", 0),
        removed_line_count=data.get("removed_line_count", 0),
        keywords=data.get("keywords", []),
        hunks=[_deserialize_hunk(h) for h in data.get("hunks", [])],
        blob_url=data.get("blob_url", ""),
        raw_url=data.get("raw_url", ""),
        contents_url=data.get("contents_url", ""),
    )


def _deserialize_pr_metadata(data: dict[str, Any]) -> PRMetadata:
    return PRMetadata(
        title=data["title"],
        body=data.get("body", ""),
        author=data["author"],
        url=data["url"],
        state=data["state"],
        merged=data.get("merged", False),
        base_branch=data["base_branch"],
        head_branch=data["head_branch"],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
        additions=data.get("additions", 0),
        deletions=data.get("deletions", 0),
        changed_files=data.get("changed_files", 0),
        labels=data.get("labels", []),
        assignees=data.get("assignees", []),
        requested_reviewers=data.get("requested_reviewers", []),
    )


def _deserialize_commits(data: dict[str, Any]) -> CommitsData:
    return CommitsData(
        head_sha=data["head_sha"],
        commits=[
            CommitInfo(
                sha=c["sha"],
                message=c.get("message", ""),
                author=c.get("author", ""),
                date=c.get("date", ""),
            )
            for c in data.get("commits", [])
        ],
    )


def _deserialize_derived(data: dict[str, Any]) -> DerivedSignals:
    return DerivedSignals(
        total_hunks=data.get("total_hunks", 0),
        source_files_changed=data.get("source_files_changed", 0),
        test_files_changed=data.get("test_files_changed", 0),
        docs_only=data.get("docs_only", False),
        has_source_without_tests=data.get("has_source_without_tests", False),
        high_risk_files=data.get("high_risk_files", []),
    )


def deserialize_context(record: ContextRecord) -> PRContext:
    """Reconstruct a ``PRContext`` from a durable ``ContextRecord``.

    The reconstructed context is sufficient for status display, inspection,
    and re-planning.  Hunk line counts in ``FileEntry`` may be lower than
    the original if the serializer bounded them.
    """
    return PRContext(
        context_id=record.context_id,
        source="persisted",
        fetched_at=record.created_at,
        cache_key=f"{record.owner}/{record.repo}/{record.pull_number}/{record.head_sha}",
        owner=record.owner,
        repo=record.repo,
        pull_number=record.pull_number,
        pr=_deserialize_pr_metadata(record.pr_metadata),
        commits=_deserialize_commits(record.commits),
        files=[_deserialize_file_entry(f) for f in record.files],
        derived=_deserialize_derived(record.derived) if record.derived else None,
    )
