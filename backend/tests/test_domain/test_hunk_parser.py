import pytest
from backend.domain.pr_context.hunk_parser import parse_patch, parse_hunk_header, Hunk, HunkLine


class TestParseHunkHeader:
    def test_standard_header(self):
        old_s, old_l, new_s, new_l = parse_hunk_header("@@ -10,5 +20,7 @@")
        assert old_s == 10
        assert old_l == 5
        assert new_s == 20
        assert new_l == 7

    def test_header_without_counts(self):
        old_s, old_l, new_s, new_l = parse_hunk_header("@@ -1 +1 @@")
        assert old_s == 1
        assert old_l == 1
        assert new_s == 1
        assert new_l == 1

    def test_header_with_context(self):
        old_s, old_l, new_s, new_l = parse_hunk_header("@@ -1,3 +1,5 @@ some context")
        assert old_s == 1
        assert new_s == 1

    def test_invalid_header(self):
        with pytest.raises(ValueError, match="Invalid hunk header"):
            parse_hunk_header("not a header")


class TestParsePatch:
    def test_empty_patch(self):
        assert parse_patch(None) == []
        assert parse_patch("") == []

    def test_single_hunk(self):
        patch = "@@ -1,3 +1,4 @@\n line1\n+added\n line2\n line3"
        hunks = parse_patch(patch)
        assert len(hunks) == 1
        assert hunks[0].old_start == 1
        assert len(hunks[0].lines) == 4
        added = [l for l in hunks[0].lines if l.type == "added"]
        assert len(added) == 1
        assert added[0].content == "added"
        assert added[0].new_line == 2

    def test_multiple_hunks(self):
        patch = "@@ -1,2 +1,3 @@\n line1\n+added1\n line2\n@@ -10,2 +11,3 @@\n line10\n+added2\n line11"
        hunks = parse_patch(patch)
        assert len(hunks) == 2
        assert hunks[0].old_start == 1
        assert hunks[1].old_start == 10

    def test_removed_lines(self):
        patch = "@@ -1,3 +1,2 @@\n line1\n-removed\n line2"
        hunks = parse_patch(patch)
        removed = [l for l in hunks[0].lines if l.type == "removed"]
        assert len(removed) == 1
        assert removed[0].content == "removed"
        assert removed[0].old_line == 2
        assert removed[0].new_line is None

    def test_context_lines(self):
        patch = "@@ -1,3 +1,3 @@\n line1\n line2\n line3"
        hunks = parse_patch(patch)
        context = [l for l in hunks[0].lines if l.type == "context"]
        assert len(context) == 3

    def test_no_newline_at_eof(self):
        patch = "@@ -1,2 +1,2 @@\n line1\n-line2\n+line2_modified\n\\ No newline at end of file"
        hunks = parse_patch(patch)
        assert len(hunks[0].lines) == 3
