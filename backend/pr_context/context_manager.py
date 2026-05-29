import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .fetcher import PRMetadata, CommitsData, ChangedFile, fetch_pr_metadata, fetch_commits, fetch_changed_files
from .hunk_parser import Hunk
from .classifier import Classification, classify_file
from .scorer import compute_priority_score
from .edge_handler import ProcessedFile, process_file


@dataclass
class FileEntry:
    filename: str
    previous_filename: str | None
    status: str
    additions: int
    deletions: int
    changes: int
    language: str
    language_family: str
    rule_profile: str
    is_test: bool
    is_docs: bool
    is_config: bool
    is_source: bool
    is_generated: bool
    is_binary: bool
    patch_available: bool
    large_patch: bool
    parse_error: str | None
    is_high_risk_path: bool
    risk_hints: list[str] = field(default_factory=list)
    priority_score_hint: int = 0
    hunk_count: int = 0
    added_line_count: int = 0
    removed_line_count: int = 0
    keywords: list[str] = field(default_factory=list)
    hunks: list[Hunk] = field(default_factory=list)
    blob_url: str = ""
    raw_url: str = ""
    contents_url: str = ""


@dataclass
class DerivedSignals:
    total_hunks: int
    source_files_changed: int
    test_files_changed: int
    docs_only: bool
    has_source_without_tests: bool
    high_risk_files: list[str] = field(default_factory=list)


@dataclass
class PRContext:
    context_id: str
    source: str
    fetched_at: str
    cache_key: str
    pr: PRMetadata
    commits: CommitsData
    files: list[FileEntry] = field(default_factory=list)
    derived: DerivedSignals | None = None


# In-memory storage
_contexts: dict[str, PRContext] = {}


_RISK_KEYWORDS = frozenset({
    "token", "password", "secret", "api_key", "apikey",
    "auth", "permission", "credential", "private",
    "sql", "query", "execute", "eval", "exec",
    "subprocess", "shell", "os.system",
    "except", "catch", "TODO", "FIXME", "HACK",
})


def _extract_keywords(hunks: list[Hunk]) -> list[str]:
    """Extract risk-related keywords from hunk content."""
    found = set()
    for hunk in hunks:
        for line in hunk.lines:
            if line.type != "added":
                continue
            content_lower = line.content.lower()
            for kw in _RISK_KEYWORDS:
                if kw.lower() in content_lower:
                    found.add(kw)
    return sorted(found)


def _build_file_entries(files: list[ChangedFile]) -> list[FileEntry]:
    """Process all files through the full pipeline."""
    all_filenames = {f.filename for f in files}
    entries: list[FileEntry] = []

    for file in files:
        # Edge handling
        processed: ProcessedFile = process_file(file)

        # Classification
        classification: Classification = classify_file(file, all_filenames)

        # Scoring
        score = compute_priority_score(
            classification, file.additions, file.deletions, file.status,
        )

        # Compute per-file stats
        hunk_count = len(processed.hunks)
        added = sum(1 for h in processed.hunks for l in h.lines if l.type == "added")
        removed = sum(1 for h in processed.hunks for l in h.lines if l.type == "removed")
        keywords = _extract_keywords(processed.hunks)

        entries.append(FileEntry(
            filename=processed.filename,
            previous_filename=processed.previous_filename,
            status=processed.status,
            additions=processed.additions,
            deletions=processed.deletions,
            changes=processed.changes,
            language=classification.language,
            language_family=classification.language_family,
            rule_profile=classification.rule_profile,
            is_test=classification.is_test,
            is_docs=classification.is_docs,
            is_config=classification.is_config,
            is_source=classification.is_source,
            is_generated=classification.is_generated,
            is_binary=processed.is_binary,
            patch_available=processed.patch_available,
            large_patch=processed.large_patch,
            parse_error=processed.parse_error,
            is_high_risk_path=classification.is_high_risk_path,
            risk_hints=classification.risk_hints,
            priority_score_hint=score,
            hunk_count=hunk_count,
            added_line_count=added,
            removed_line_count=removed,
            keywords=keywords,
            hunks=processed.hunks,
            blob_url=processed.blob_url,
            raw_url=processed.raw_url,
            contents_url=processed.contents_url,
        ))

    return entries


def compute_derived(files: list[FileEntry]) -> DerivedSignals:
    """Compute aggregate signals from parsed file data."""
    total_hunks = sum(f.hunk_count for f in files)
    source_files_changed = sum(1 for f in files if f.is_source)
    test_files_changed = sum(1 for f in files if f.is_test)
    docs_only = all(f.is_docs for f in files) if files else False
    has_source_without_tests = (
        source_files_changed > 0 and test_files_changed == 0
    )
    high_risk_files = [f.filename for f in files if f.is_high_risk_path]

    return DerivedSignals(
        total_hunks=total_hunks,
        source_files_changed=source_files_changed,
        test_files_changed=test_files_changed,
        docs_only=docs_only,
        has_source_without_tests=has_source_without_tests,
        high_risk_files=high_risk_files,
    )


async def build_pr_context(
    pr_metadata_raw: dict,
    commits_raw: list[dict],
    files_raw: list[dict],
) -> PRContext:
    """Build a complete PRContext from raw GitHub API data."""
    pr = fetch_pr_metadata(pr_metadata_raw)
    commits = fetch_commits(commits_raw)
    changed_files = fetch_changed_files(files_raw)

    file_entries = _build_file_entries(changed_files)
    derived = compute_derived(file_entries)

    context_id = f"ctx_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    cache_key = f"{pr.author}/{pr.base_branch}/{commits.head_sha}"

    ctx = PRContext(
        context_id=context_id,
        source="github",
        fetched_at=now,
        cache_key=cache_key,
        pr=pr,
        commits=commits,
        files=file_entries,
        derived=derived,
    )

    _contexts[context_id] = ctx
    return ctx


def get_context(context_id: str) -> PRContext | None:
    """Retrieve a PRContext by ID."""
    return _contexts.get(context_id)


def get_overview_view(ctx: PRContext) -> dict:
    """Return Overview View without patch/hunk data."""
    return {
        "context_id": ctx.context_id,
        "pr": {
            "title": ctx.pr.title,
            "author": ctx.pr.author,
            "url": ctx.pr.url,
            "base_branch": ctx.pr.base_branch,
            "head_branch": ctx.pr.head_branch,
            "additions": ctx.pr.additions,
            "deletions": ctx.pr.deletions,
            "changed_files": ctx.pr.changed_files,
            "head_sha": ctx.commits.head_sha,
        },
        "files": [
            {
                "filename": f.filename,
                "status": f.status,
                "additions": f.additions,
                "deletions": f.deletions,
                "language": f.language,
                "language_family": f.language_family,
                "is_test": f.is_test,
                "is_docs": f.is_docs,
                "is_config": f.is_config,
                "is_source": f.is_source,
                "is_binary": f.is_binary,
                "is_high_risk_path": f.is_high_risk_path,
                "risk_hints": f.risk_hints,
                "priority_score_hint": f.priority_score_hint,
            }
            for f in ctx.files
        ],
        "derived": {
            "docs_only": ctx.derived.docs_only if ctx.derived else False,
            "has_source_without_tests": ctx.derived.has_source_without_tests if ctx.derived else False,
            "high_risk_files": ctx.derived.high_risk_files if ctx.derived else [],
        },
    }


def get_patch_index_view(ctx: PRContext) -> dict:
    """Return Patch Index View sorted by priority_score_hint descending."""
    sorted_files = sorted(ctx.files, key=lambda f: f.priority_score_hint, reverse=True)
    return {
        "context_id": ctx.context_id,
        "files": [
            {
                "filename": f.filename,
                "hunk_count": f.hunk_count,
                "added_line_count": f.added_line_count,
                "removed_line_count": f.removed_line_count,
                "keywords": f.keywords,
                "risk_score_hint": f.priority_score_hint,
            }
            for f in sorted_files
        ],
    }


def get_file_patch(ctx: PRContext, filename: str, hunk_index: int | None = None) -> dict:
    """Return patch data for a specific file, optionally filtered by hunk index."""
    entry = None
    for f in ctx.files:
        if f.filename == filename:
            entry = f
            break
    if entry is None:
        raise KeyError(f"File not found: {filename}")

    hunks = entry.hunks
    if hunk_index is not None:
        if hunk_index < 0 or hunk_index >= len(hunks):
            raise IndexError(f"Hunk index {hunk_index} out of range (0-{len(hunks)-1})")
        hunks = [hunks[hunk_index]]

    return {
        "context_id": ctx.context_id,
        "filename": entry.filename,
        "patch_available": entry.patch_available,
        "is_binary": entry.is_binary,
        "parse_error": entry.parse_error,
        "hunks": [
            {
                "header": h.header,
                "old_start": h.old_start,
                "old_lines": h.old_lines,
                "new_start": h.new_start,
                "new_lines": h.new_lines,
                "lines": [
                    {
                        "type": l.type,
                        "content": l.content,
                        "old_line": l.old_line,
                        "new_line": l.new_line,
                    }
                    for l in h.lines
                ],
            }
            for h in hunks
        ],
    }
