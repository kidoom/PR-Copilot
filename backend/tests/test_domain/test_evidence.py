import pytest
from backend.domain.review.evidence import (
    analyze,
    deduplicate,
    sort_evidence,
    sanitize_excerpt,
    EvidenceItem,
    Severity,
)
from backend.domain.pr_context.context_manager import (
    PRContext, FileEntry, DerivedSignals, build_pr_context,
)
from backend.domain.pr_context.hunk_parser import Hunk, HunkLine
from backend.domain.pr_context.fetcher import PRMetadata, CommitsData


def _make_context(files: list[FileEntry], derived: DerivedSignals | None = None) -> PRContext:
    return PRContext(
        context_id="ctx_test", source="test", fetched_at="2026-01-01",
        cache_key="test", owner="o", repo="r", pull_number=1,
        pr=PRMetadata(
            title="Test PR", body="", author="a", url="http://example.com",
            state="open", merged=False, base_branch="main", head_branch="feat",
            created_at="", updated_at="", additions=10, deletions=5, changed_files=1,
        ),
        commits=CommitsData(head_sha="abc123", commits=[]),
        files=files,
        derived=derived,
    )


def _make_file_entry(filename: str, **overrides) -> FileEntry:
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


class TestAnalyze:
    def test_secret_detection(self):
        hunk = Hunk(
            header="@@ -1,1 +1,2 @@", old_start=1, old_lines=1, new_start=1, new_lines=2,
            lines=[
                HunkLine(type="context", content="existing line", old_line=1, new_line=1),
                HunkLine(type="added", content='secret = "ghp_abcdef1234567890abcdef1234567890abcdef"', old_line=None, new_line=2),
            ],
        )
        file = _make_file_entry("config.py", hunks=[hunk])
        ctx = _make_context([file])
        items = analyze(ctx)
        security_items = [i for i in items if i.category == "security"]
        assert len(security_items) >= 1

    def test_bare_except_detection(self):
        hunk = Hunk(
            header="@@ -1,1 +1,2 @@", old_start=1, old_lines=1, new_start=1, new_lines=2,
            lines=[
                HunkLine(type="context", content="try:", old_line=1, new_line=1),
                HunkLine(type="added", content="    pass", old_line=None, new_line=2),
                HunkLine(type="added", content="except:", old_line=None, new_line=3),
            ],
        )
        file = _make_file_entry("main.py", hunks=[hunk])
        ctx = _make_context([file])
        items = analyze(ctx)
        bare_except = [i for i in items if i.rule_id == "bare_except"]
        assert len(bare_except) == 1

    def test_bare_except_ignored_for_non_python(self):
        hunk = Hunk(
            header="@@ -1,1 +1,1 @@", old_start=1, old_lines=1, new_start=1, new_lines=1,
            lines=[
                HunkLine(type="added", content="except:", old_line=None, new_line=1),
            ],
        )
        file = _make_file_entry("app.js", language="javascript", hunks=[hunk])
        ctx = _make_context([file])
        items = analyze(ctx)
        bare_except = [i for i in items if i.rule_id == "bare_except"]
        assert len(bare_except) == 0

    def test_high_risk_path_evidence(self):
        file = _make_file_entry("src/auth/login.py", is_high_risk_path=True, risk_hints=["auth_path"])
        ctx = _make_context([file])
        items = analyze(ctx)
        hrp = [i for i in items if i.rule_id == "high_risk_path"]
        assert len(hrp) == 1

    def test_source_without_tests(self):
        files = [_make_file_entry("src/main.py", is_source=True)]
        derived = DerivedSignals(
            total_hunks=1, source_files_changed=1, test_files_changed=0,
            docs_only=False, has_source_without_tests=True,
        )
        ctx = _make_context(files, derived)
        items = analyze(ctx)
        swt = [i for i in items if i.rule_id == "source_without_tests"]
        assert len(swt) == 1


class TestDeduplicate:
    def test_removes_duplicates(self):
        items = [
            EvidenceItem(id="1", source="s", rule_id="r", file="f", severity="warning", category="security", message="msg", confidence=0.8),
            EvidenceItem(id="2", source="s", rule_id="r", file="f", severity="warning", category="security", message="msg", confidence=0.8),
        ]
        result = deduplicate(items)
        assert len(result) == 1

    def test_keeps_different_items(self):
        items = [
            EvidenceItem(id="1", source="s", rule_id="r1", file="f", severity="warning", category="security", message="msg1", confidence=0.8),
            EvidenceItem(id="2", source="s", rule_id="r2", file="f", severity="warning", category="security", message="msg2", confidence=0.8),
        ]
        result = deduplicate(items)
        assert len(result) == 2


class TestSortEvidence:
    def test_severity_ordering(self):
        items = [
            EvidenceItem(id="1", source="s", rule_id="r", file="f", severity="info", category="c", message="m", confidence=0.5),
            EvidenceItem(id="2", source="s", rule_id="r", file="f", severity="critical", category="c", message="m", confidence=0.5),
            EvidenceItem(id="3", source="s", rule_id="r", file="f", severity="warning", category="c", message="m", confidence=0.5),
        ]
        result = sort_evidence(items)
        assert result[0].severity == "critical"
        assert result[1].severity == "warning"
        assert result[2].severity == "info"


class TestSanitizeExcerpt:
    def test_redacts_bearer_token(self):
        text = "Authorization: Bearer sk-abcdef1234567890abcdef1234567890"
        result = sanitize_excerpt(text)
        assert "sk-abcdef" not in result
        assert "REDACTED" in result

    def test_redacts_github_token(self):
        text = "token: ghp_abcdef1234567890abcdef1234567890abcdef"
        result = sanitize_excerpt(text)
        assert "ghp_abcdef" not in result
