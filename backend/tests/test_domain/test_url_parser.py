import pytest
from backend.domain.github.url_parser import parse_pr_url, ParsedPRUrl


class TestParsePRUrl:
    def test_valid_url(self):
        result = parse_pr_url("https://github.com/owner/repo/pull/42")
        assert result == ParsedPRUrl(owner="owner", repo="repo", pull_number=42)

    def test_valid_url_with_trailing_slash(self):
        result = parse_pr_url("https://github.com/owner/repo/pull/42/")
        assert result.owner == "owner"
        assert result.pull_number == 42

    def test_valid_url_with_extra_path(self):
        result = parse_pr_url("https://github.com/owner/repo/pull/42/files")
        assert result.owner == "owner"
        assert result.pull_number == 42

    def test_http_url(self):
        result = parse_pr_url("http://github.com/owner/repo/pull/1")
        assert result.pull_number == 1

    def test_invalid_url_not_github(self):
        with pytest.raises(ValueError, match="Invalid GitHub PR URL"):
            parse_pr_url("https://gitlab.com/owner/repo/pull/1")

    def test_invalid_url_no_pull(self):
        with pytest.raises(ValueError, match="Invalid GitHub PR URL"):
            parse_pr_url("https://github.com/owner/repo/issues/1")

    def test_invalid_url_empty(self):
        with pytest.raises(ValueError):
            parse_pr_url("")

    def test_invalid_pull_number_zero(self):
        with pytest.raises(ValueError, match="Invalid pull number"):
            parse_pr_url("https://github.com/owner/repo/pull/0")

    def test_invalid_pull_number_negative(self):
        with pytest.raises(ValueError, match="Invalid GitHub PR URL"):
            parse_pr_url("https://github.com/owner/repo/pull/-1")
