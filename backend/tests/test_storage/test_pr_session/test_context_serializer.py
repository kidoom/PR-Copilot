"""Tests for PR context serialization: round-trip, size boundary, corruption."""

from __future__ import annotations

import pytest

from backend.domain.pr_context.context_manager import (
    DerivedSignals,
    FileEntry,
    PRContext,
)
from backend.domain.pr_context.fetcher import CommitInfo, CommitsData, PRMetadata
from backend.domain.pr_context.hunk_parser import Hunk, HunkLine
from backend.storage.pr_session.context_serializer import (
    deserialize_context,
    serialize_context,
)
from backend.storage.pr_session.models import ContextRecord


def _make_pr_metadata(**overrides) -> PRMetadata:
    defaults = dict(
        title="Fix bug",
        body="This PR fixes a bug.",
        author="octocat",
        url="https://github.com/octocat/hello-world/pull/42",
        state="open",
        merged=False,
        base_branch="main",
        head_branch="fix-bug",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T01:00:00Z",
        additions=10,
        deletions=5,
        changed_files=2,
        labels=["bug"],
        assignees=[],
        requested_reviewers=[],
    )
    defaults.update(overrides)
    return PRMetadata(**defaults)


def _make_commits(head_sha: str = "bbb222") -> CommitsData:
    return CommitsData(
        head_sha=head_sha,
        commits=[
            CommitInfo(sha="aaa111", message="first commit", author="a", date="2026-01-01"),
            CommitInfo(sha=head_sha, message="second commit", author="a", date="2026-01-02"),
        ],
    )


def _make_hunks() -> list[Hunk]:
    return [
        Hunk(
            header="@@ -1,3 +1,4 @@",
            old_start=1, old_lines=3,
            new_start=1, new_lines=4,
            lines=[
                HunkLine(type="context", content="unchanged", old_line=1, new_line=1),
                HunkLine(type="added", content="new line", old_line=None, new_line=2),
                HunkLine(type="removed", content="old line", old_line=2, new_line=None),
                HunkLine(type="context", content="also unchanged", old_line=3, new_line=3),
            ],
        )
    ]


def _make_file_entry(**overrides) -> FileEntry:
    defaults = dict(
        filename="src/main.py",
        previous_filename=None,
        status="modified",
        additions=5,
        deletions=2,
        changes=7,
        language="Python",
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
        priority_score_hint=70,
        hunk_count=1,
        added_line_count=1,
        removed_line_count=1,
        keywords=["bug"],
        hunks=_make_hunks(),
    )
    defaults.update(overrides)
    return FileEntry(**defaults)


def _make_context(**overrides) -> PRContext:
    defaults = dict(
        context_id="ctx-1",
        source="github",
        fetched_at="2026-01-01T00:00:00Z",
        cache_key="octocat/hello-world/42/bbb222",
        owner="octocat",
        repo="hello-world",
        pull_number=42,
        pr=_make_pr_metadata(),
        commits=_make_commits(),
        files=[_make_file_entry()],
        derived=DerivedSignals(
            total_hunks=1,
            source_files_changed=1,
            test_files_changed=0,
            docs_only=False,
            has_source_without_tests=True,
            high_risk_files=[],
        ),
    )
    defaults.update(overrides)
    return PRContext(**defaults)


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------


class TestContextRoundTrip:
    def test_basic_roundtrip(self):
        ctx = _make_context()
        record = serialize_context(ctx, "ps-1")
        restored = deserialize_context(record)

        assert restored.context_id == ctx.context_id
        assert restored.owner == ctx.owner
        assert restored.repo == ctx.repo
        assert restored.pull_number == ctx.pull_number
        assert restored.pr.title == ctx.pr.title
        assert restored.commits.head_sha == ctx.commits.head_sha
        assert len(restored.files) == 1
        assert restored.files[0].filename == "src/main.py"

    def test_roundtrip_preserves_hunks(self):
        ctx = _make_context()
        record = serialize_context(ctx, "ps-1")
        restored = deserialize_context(record)

        assert len(restored.files[0].hunks) == 1
        hunk = restored.files[0].hunks[0]
        assert hunk.header == "@@ -1,3 +1,4 @@"
        assert len(hunk.lines) == 4
        assert hunk.lines[0].type == "context"
        assert hunk.lines[1].type == "added"
        assert hunk.lines[1].content == "new line"

    def test_roundtrip_preserves_derived_signals(self):
        ctx = _make_context()
        record = serialize_context(ctx, "ps-1")
        restored = deserialize_context(record)

        assert restored.derived is not None
        assert restored.derived.total_hunks == 1
        assert restored.derived.has_source_without_tests is True

    def test_roundtrip_preserves_classification_flags(self):
        ctx = _make_context()
        record = serialize_context(ctx, "ps-1")
        restored = deserialize_context(record)

        f = restored.files[0]
        assert f.is_source is True
        assert f.is_test is False
        assert f.language == "Python"

    def test_roundtrip_with_multiple_files(self):
        f1 = _make_file_entry(filename="a.py", priority_score_hint=80)
        f2 = _make_file_entry(filename="b.py", priority_score_hint=50, hunks=[])
        ctx = _make_context(files=[f1, f2])
        record = serialize_context(ctx, "ps-1")
        restored = deserialize_context(record)

        assert len(restored.files) == 2
        assert restored.files[0].filename == "a.py"
        assert restored.files[1].filename == "b.py"

    def test_roundtrip_with_previous_filename(self):
        f = _make_file_entry(
            filename="new_name.py",
            previous_filename="old_name.py",
            status="renamed",
        )
        ctx = _make_context(files=[f])
        record = serialize_context(ctx, "ps-1")
        restored = deserialize_context(record)
        assert restored.files[0].previous_filename == "old_name.py"

    def test_roundtrip_with_no_derived(self):
        ctx = _make_context(derived=None)
        record = serialize_context(ctx, "ps-1")
        restored = deserialize_context(record)
        # derived is None when the original had no derived signals
        assert restored.derived is None


# ---------------------------------------------------------------------------
# Size boundary tests
# ---------------------------------------------------------------------------


class TestSizeBoundary:
    def test_hunk_lines_bounded(self):
        """Hunks with many lines are truncated to max_hunk_lines."""
        many_lines = [
            HunkLine(type="added", content=f"line {i}", old_line=None, new_line=i)
            for i in range(500)
        ]
        big_hunk = Hunk(
            header="@@ -1,500 +1,500 @@",
            old_start=1, old_lines=500,
            new_start=1, new_lines=500,
            lines=many_lines,
        )
        f = _make_file_entry(hunks=[big_hunk], hunk_count=1, added_line_count=500)
        ctx = _make_context(files=[f])

        record = serialize_context(ctx, "ps-1", max_hunk_lines=50)
        restored = deserialize_context(record)

        total_lines = sum(len(h.lines) for h in restored.files[0].hunks)
        assert total_lines <= 50

    def test_pr_body_bounded(self):
        """PR body is truncated to 2000 characters."""
        long_body = "x" * 5000
        pr = _make_pr_metadata(body=long_body)
        ctx = _make_context(pr=pr)
        record = serialize_context(ctx, "ps-1")
        assert len(record.pr_metadata["body"]) <= 2000

    def test_commit_message_bounded(self):
        """Commit messages are truncated to 500 characters."""
        long_msg = "x" * 1000
        commits = CommitsData(
            head_sha="abc",
            commits=[CommitInfo(sha="abc", message=long_msg, author="a", date="d")],
        )
        ctx = _make_context(commits=commits)
        record = serialize_context(ctx, "ps-1")
        assert len(record.commits["commits"][0]["message"]) <= 500


# ---------------------------------------------------------------------------
# ContextRecord model tests
# ---------------------------------------------------------------------------


class TestContextRecordModel:
    def test_record_to_dict_roundtrip(self):
        record = ContextRecord(
            context_id="ctx-1",
            pr_session_id="ps-1",
            owner="octocat",
            repo="hello",
            pull_number=1,
            base_sha="aaa",
            head_sha="bbb",
        )
        d = record.to_dict()
        restored = ContextRecord.from_dict(d)
        assert restored.context_id == "ctx-1"
        assert restored.schema_version == 1

    def test_record_schema_version(self):
        record = ContextRecord(
            context_id="c", pr_session_id="ps",
            owner="o", repo="r", pull_number=1,
            base_sha="a", head_sha="b",
        )
        d = record.to_dict()
        assert d["schema_version"] == 1
