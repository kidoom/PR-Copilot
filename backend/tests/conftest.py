import pytest

from backend.pr_context.context_manager import FileEntry, DerivedSignals, PRContext, PRMetadata, CommitsData


def _make_file(**overrides) -> FileEntry:
    defaults = dict(
        filename="src/main.py",
        previous_filename=None,
        status="modified",
        additions=10,
        deletions=5,
        changes=15,
        language="python",
        language_family="python",
        rule_profile="source",
        is_test=False,
        is_docs=False,
        is_config=False,
        is_source=True,
        is_generated=False,
        is_binary=False,
        patch_available=True,
        large_patch=False,
        parse_error=None,
        is_high_risk_path=False,
        risk_hints=[],
        priority_score_hint=50,
        hunk_count=1,
        added_line_count=10,
        removed_line_count=5,
        keywords=[],
        hunks=[],
        blob_url="",
        raw_url="",
        contents_url="",
    )
    defaults.update(overrides)
    return FileEntry(**defaults)


def _make_pr(**overrides) -> PRMetadata:
    defaults = dict(
        title="Test PR",
        body="",
        author="testuser",
        url="https://github.com/owner/repo/pull/1",
        state="open",
        merged=False,
        base_branch="main",
        head_branch="feature",
        created_at="2025-01-01T00:00:00Z",
        updated_at="2025-01-01T00:00:00Z",
        additions=10,
        deletions=5,
        changed_files=1,
        labels=[],
        assignees=[],
        requested_reviewers=[],
    )
    defaults.update(overrides)
    return PRMetadata(**defaults)


def _make_context(files=None, derived=None, pr_kwargs=None) -> PRContext:
    if files is None:
        files = [_make_file()]
    if pr_kwargs is None:
        pr_kwargs = {}
    pr = _make_pr(**pr_kwargs)
    commits = CommitsData(head_sha="abc123", commits=[])
    return PRContext(
        context_id="ctx_test123",
        source="github",
        fetched_at="2025-01-01T00:00:00Z",
        cache_key="owner/repo/1/abc123",
        owner="owner",
        repo="repo",
        pull_number=1,
        pr=pr,
        commits=commits,
        files=files,
        derived=derived,
    )
