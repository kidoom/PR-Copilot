"""Tests for local GitHub credential resolver."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from backend.domain.github.local_credentials import (
    CredentialSource,
    ResolvedCredential,
    resolve_github_token,
)


class TestResolveGitHubToken:
    """Test credential resolution precedence."""

    def test_gh_token_env_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "ghp_from_env")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_other")

        result = resolve_github_token()

        assert result.token == "ghp_from_env"
        assert result.source == CredentialSource.GH_TOKEN
        assert result.is_authenticated is True

    def test_github_token_env_fallback(self, monkeypatch):
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_github_token")

        result = resolve_github_token()

        assert result.token == "ghp_github_token"
        assert result.source == CredentialSource.GITHUB_TOKEN
        assert result.is_authenticated is True

    def test_gh_cli_fallback(self, monkeypatch):
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        with patch("backend.domain.github.local_credentials._try_gh_auth_token", return_value="ghp_from_cli"):
            result = resolve_github_token()

        assert result.token == "ghp_from_cli"
        assert result.source == CredentialSource.GH_CLI
        assert result.is_authenticated is True

    def test_anonymous_when_nothing_available(self, monkeypatch):
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        with patch("backend.domain.github.local_credentials._try_gh_auth_token", return_value=None):
            result = resolve_github_token()

        assert result.token == ""
        assert result.source == CredentialSource.ANONYMOUS
        assert result.is_authenticated is False

    def test_empty_env_var_treated_as_missing(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "  ")
        monkeypatch.setenv("GITHUB_TOKEN", "")

        with patch("backend.domain.github.local_credentials._try_gh_auth_token", return_value=None):
            result = resolve_github_token()

        assert result.source == CredentialSource.ANONYMOUS


class TestTryGhAuthToken:
    """Test gh CLI token resolution."""

    def test_returns_token_on_success(self):
        with patch("backend.domain.github.local_credentials.shutil.which", return_value="/usr/bin/gh"):
            with patch("backend.domain.github.local_credentials.subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["gh", "auth", "token"],
                    returncode=0,
                    stdout="ghp_clitoken123\n",
                    stderr="",
                )
                from backend.domain.github.local_credentials import _try_gh_auth_token
                result = _try_gh_auth_token()

        assert result == "ghp_clitoken123"

    def test_returns_none_when_gh_missing(self):
        with patch("backend.domain.github.local_credentials.shutil.which", return_value=None):
            from backend.domain.github.local_credentials import _try_gh_auth_token
            result = _try_gh_auth_token()

        assert result is None

    def test_returns_none_when_not_authenticated(self):
        with patch("backend.domain.github.local_credentials.shutil.which", return_value="/usr/bin/gh"):
            with patch("backend.domain.github.local_credentials.subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["gh", "auth", "token"],
                    returncode=1,
                    stdout="",
                    stderr="not logged in",
                )
                from backend.domain.github.local_credentials import _try_gh_auth_token
                result = _try_gh_auth_token()

        assert result is None

    def test_returns_none_on_timeout(self):
        with patch("backend.domain.github.local_credentials.shutil.which", return_value="/usr/bin/gh"):
            with patch("backend.domain.github.local_credentials.subprocess.run", side_effect=subprocess.TimeoutExpired("gh", 5)):
                from backend.domain.github.local_credentials import _try_gh_auth_token
                result = _try_gh_auth_token()

        assert result is None

    def test_returns_none_on_os_error(self):
        with patch("backend.domain.github.local_credentials.shutil.which", return_value="/usr/bin/gh"):
            with patch("backend.domain.github.local_credentials.subprocess.run", side_effect=OSError("no such file")):
                from backend.domain.github.local_credentials import _try_gh_auth_token
                result = _try_gh_auth_token()

        assert result is None

    def test_returns_none_when_output_empty(self):
        with patch("backend.domain.github.local_credentials.shutil.which", return_value="/usr/bin/gh"):
            with patch("backend.domain.github.local_credentials.subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["gh", "auth", "token"],
                    returncode=0,
                    stdout="",
                    stderr="",
                )
                from backend.domain.github.local_credentials import _try_gh_auth_token
                result = _try_gh_auth_token()

        assert result is None


class TestResolvedCredential:
    def test_is_authenticated_true(self):
        cred = ResolvedCredential(token="ghp_abc", source=CredentialSource.GH_TOKEN)
        assert cred.is_authenticated is True

    def test_is_authenticated_false_empty(self):
        cred = ResolvedCredential(token="", source=CredentialSource.ANONYMOUS)
        assert cred.is_authenticated is False
