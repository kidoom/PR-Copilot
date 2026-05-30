import pytest
from backend.domain.review.intake import (
    classify_size,
    classify_change_type,
    build_intake_summary,
)
from backend.domain.pr_context.context_manager import (
    PRContext, FileEntry, DerivedSignals,
)
from backend.domain.pr_context.fetcher import PRMetadata, CommitsData


def _make_pr(changed_files: int = 5, additions: int = 100, deletions: int = 50) -> PRMetadata:
    return PRMetadata(
        title="Test PR", body="", author="a", url="http://example.com",
        state="open", merged=False, base_branch="main", head_branch="feat",
        created_at="", updated_at="",
        additions=additions, deletions=deletions, changed_files=changed_files,
    )


def _make_context(files: list[FileEntry], pr: PRMetadata | None = None, derived: DerivedSignals | None = None) -> PRContext:
    return PRContext(
        context_id="ctx_test", source="test", fetched_at="2026-01-01",
        cache_key="test", owner="o", repo="r", pull_number=1,
        pr=pr or _make_pr(),
        commits=CommitsData(head_sha="abc123", commits=[]),
        files=files,
        derived=derived,
    )


def _make_file(filename: str, **overrides) -> FileEntry:
    defaults = dict(
        filename=filename, previous_filename=None, status="modified",
        additions=10, deletions=5, changes=15,
        language="python", language_family="backend", rule_profile="python",
        is_test=False, is_docs=False, is_config=False, is_source=True,
        is_generated=False, is_binary=False, patch_available=True,
        large_patch=False, parse_error=None, is_high_risk_path=False,
        risk_hints=[], priority_score_hint=50, hunk_count=1,
        added_line_count=5, removed_line_count=2, keywords=[], hunks=[],
        blob_url="", raw_url="", contents_url="",
    )
    defaults.update(overrides)
    return FileEntry(**defaults)


class TestClassifySize:
    def test_small(self):
        pr = _make_pr(changed_files=2, additions=50, deletions=30)
        assert classify_size(_make_context([], pr)) == "small"

    def test_medium(self):
        pr = _make_pr(changed_files=8, additions=200, deletions=100)
        assert classify_size(_make_context([], pr)) == "medium"

    def test_large(self):
        pr = _make_pr(changed_files=15, additions=600, deletions=400)
        assert classify_size(_make_context([], pr)) == "large"


class TestClassifyChangeType:
    def test_docs_only(self):
        files = [_make_file("README.md", is_docs=True, is_source=False)]
        assert classify_change_type(files) == "docs"

    def test_test_only(self):
        files = [_make_file("test_main.py", is_test=True, is_source=False)]
        assert classify_change_type(files) == "test"

    def test_config_only(self):
        files = [_make_file("config.yaml", is_config=True, is_source=False)]
        assert classify_change_type(files) == "config"

    def test_source(self):
        files = [
            _make_file("src/main.py", is_source=True),
            _make_file("src/utils.py", is_source=True),
        ]
        assert classify_change_type(files) == "source"

    def test_mixed(self):
        files = [
            _make_file("src/main.py", is_source=True),
            _make_file("test_main.py", is_test=True, is_source=False),
        ]
        assert classify_change_type(files) == "mixed"

    def test_empty(self):
        assert classify_change_type([]) == "mixed"


class TestBuildIntakeSummary:
    def test_returns_all_fields(self):
        files = [_make_file("src/main.py")]
        derived = DerivedSignals(
            total_hunks=1, source_files_changed=1, test_files_changed=0,
            docs_only=False, has_source_without_tests=True, high_risk_files=[],
        )
        ctx = _make_context(files, derived=derived)
        result = build_intake_summary(ctx)
        assert "context_id" in result
        assert "size" in result
        assert "change_type" in result
        assert "language_distribution" in result
        assert "file_type_distribution" in result
        assert "top_directories" in result
        assert "notable_signals" in result
        assert "source_without_tests" in result["notable_signals"]
