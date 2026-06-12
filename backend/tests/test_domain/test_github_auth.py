from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

import pytest

from backend.domain.github.auth import (
    GitHubAppAuthConfig,
    GitHubAuthError,
    GitHubAuthService,
)


class FakeOAuthClient:
    def __init__(self) -> None:
        self.exchanged_code = ""
        self.exchanged_verifier = ""
        self.refreshed_token = ""

    async def exchange_code(self, config, *, code, code_verifier):
        self.exchanged_code = code
        self.exchanged_verifier = code_verifier
        return {
            "access_token": "ghu_initial",
            "expires_in": 3600,
            "refresh_token": "ghr_initial",
            "refresh_token_expires_in": 7200,
        }

    async def refresh_access_token(self, config, *, refresh_token):
        self.refreshed_token = refresh_token
        return {
            "access_token": "ghu_refreshed",
            "expires_in": 3600,
            "refresh_token": "ghr_refreshed",
            "refresh_token_expires_in": 7200,
        }

    async def get_user(self, access_token):
        return {
            "login": "octocat",
            "name": "The Octocat",
            "avatar_url": "https://avatars.example/octocat.png",
            "html_url": "https://github.com/octocat",
        }

    async def repository_access(self, access_token, *, owner, repo):
        return {
            "authorized": owner == "octocat" and repo == "private-repo",
            "installation_id": 42 if owner == "octocat" and repo == "private-repo" else None,
        }

    async def close(self):
        pass


def _config(**overrides) -> GitHubAppAuthConfig:
    values = {
        "client_id": "Iv1.test",
        "client_secret": "secret",
        "callback_url": "http://127.0.0.1:8000/api/auth/github/callback",
        "frontend_url": "http://127.0.0.1:5173/",
        "app_slug": "pr-copilot",
    }
    values.update(overrides)
    return GitHubAppAuthConfig(**values)


def test_begin_authorization_uses_state_and_pkce():
    service = GitHubAuthService(_config(), FakeOAuthClient())

    url = urlparse(service.begin_authorization())
    query = parse_qs(url.query)

    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://github.com/login/oauth/authorize"
    assert query["client_id"] == ["Iv1.test"]
    assert query["redirect_uri"] == ["http://127.0.0.1:8000/api/auth/github/callback"]
    assert len(query["state"][0]) >= 32
    assert len(query["code_challenge"][0]) >= 43
    assert query["code_challenge_method"] == ["S256"]

def test_begin_authorization_preserves_post_auth_redirect():
    service = GitHubAuthService(_config(), FakeOAuthClient())
    state = parse_qs(
        urlparse(service.begin_authorization(post_auth_redirect="install")).query
    )["state"][0]

    assert service._pending[state].post_auth_redirect == "install"


def test_begin_authorization_requires_configuration():
    service = GitHubAuthService(_config(client_secret=""), FakeOAuthClient())

    with pytest.raises(GitHubAuthError, match="not configured"):
        service.begin_authorization()


def test_installation_url_uses_configured_app_slug():
    service = GitHubAuthService(_config(), FakeOAuthClient())

    assert service.installation_url() == "https://github.com/apps/pr-copilot/installations/new"


def test_installation_url_requires_safe_app_slug():
    service = GitHubAuthService(_config(app_slug="../bad"), FakeOAuthClient())

    with pytest.raises(GitHubAuthError, match="not configured"):
        service.installation_url()


@pytest.mark.asyncio
async def test_complete_authorization_creates_server_side_session():
    oauth = FakeOAuthClient()
    service = GitHubAuthService(_config(), oauth)
    state = parse_qs(urlparse(service.begin_authorization()).query)["state"][0]

    session = await service.complete_authorization(code="code-123", state=state)

    assert oauth.exchanged_code == "code-123"
    assert oauth.exchanged_verifier
    assert session.access_token == "ghu_initial"
    assert session.user["login"] == "octocat"
    assert await service.get_access_token(session.session_id) == "ghu_initial"

@pytest.mark.asyncio
async def test_repository_access_uses_authorized_user_token():
    service = GitHubAuthService(_config(), FakeOAuthClient())
    state = parse_qs(urlparse(service.begin_authorization()).query)["state"][0]
    session = await service.complete_authorization(code="code-123", state=state)

    result = await service.repository_access(
        session.session_id,
        owner="octocat",
        repo="private-repo",
    )

    assert result == {"authorized": True, "installation_id": 42}


@pytest.mark.asyncio
async def test_complete_authorization_rejects_unknown_state():
    service = GitHubAuthService(_config(), FakeOAuthClient())

    with pytest.raises(GitHubAuthError, match="invalid or expired"):
        await service.complete_authorization(code="code-123", state="wrong-state")


@pytest.mark.asyncio
async def test_expiring_session_refreshes_access_token():
    oauth = FakeOAuthClient()
    service = GitHubAuthService(_config(), oauth)
    state = parse_qs(urlparse(service.begin_authorization()).query)["state"][0]
    session = await service.complete_authorization(code="code-123", state=state)
    session.expires_at = time.time() - 1

    token = await service.get_access_token(session.session_id)

    assert token == "ghu_refreshed"
    assert oauth.refreshed_token == "ghr_initial"
