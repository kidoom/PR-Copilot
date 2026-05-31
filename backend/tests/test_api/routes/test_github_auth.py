from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.github_auth import router
from backend.domain.github.auth import (
    GitHubAppAuthConfig,
    GitHubAuthService,
    set_github_auth_service,
)


class FakeOAuthClient:
    async def exchange_code(self, config, *, code, code_verifier):
        return {"access_token": "ghu_test", "expires_in": 3600}

    async def refresh_access_token(self, config, *, refresh_token):
        raise AssertionError("refresh should not be called")

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


def _service(*, configured: bool = True) -> GitHubAuthService:
    return GitHubAuthService(
        GitHubAppAuthConfig(
            client_id="Iv1.test" if configured else "",
            client_secret="secret" if configured else "",
            callback_url="http://127.0.0.1:8000/api/auth/github/callback",
            frontend_url="http://127.0.0.1:5173/",
            app_slug="pr-copilot" if configured else "",
        ),
        FakeOAuthClient(),
    )


@pytest.fixture
def client():
    service = _service()
    set_github_auth_service(service)
    app = FastAPI()
    app.include_router(router)
    try:
        yield TestClient(app)
    finally:
        set_github_auth_service(None)


def test_login_redirects_to_github_with_state_and_pkce(client):
    response = client.get("/api/auth/github/login", follow_redirects=False)

    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["client_id"] == ["Iv1.test"]
    assert query["state"]
    assert query["code_challenge_method"] == ["S256"]


def test_callback_sets_http_only_session_cookie_and_session_endpoint(client):
    login = client.get("/api/auth/github/login", follow_redirects=False)
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]

    callback = client.get(
        "/api/auth/github/callback",
        params={"code": "code-123", "state": state},
        follow_redirects=False,
    )

    assert callback.status_code == 302
    assert callback.headers["location"] == "http://127.0.0.1:5173/?github_auth=success"
    assert "HttpOnly" in callback.headers["set-cookie"]
    session = client.get("/api/auth/session")
    assert session.json()["authenticated"] is True
    assert session.json()["user"]["login"] == "octocat"


def test_logout_clears_session(client):
    login = client.get("/api/auth/github/login", follow_redirects=False)
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    client.get(
        "/api/auth/github/callback",
        params={"code": "code-123", "state": state},
        follow_redirects=False,
    )

    logout = client.post("/api/auth/logout")

    assert logout.status_code == 200
    assert logout.json() == {"authenticated": False}
    assert client.get("/api/auth/session").json()["authenticated"] is False


def test_install_redirects_to_github_app_repository_selection(client):
    login = client.get("/api/auth/github/login", follow_redirects=False)
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    client.get(
        "/api/auth/github/callback",
        params={"code": "code-123", "state": state},
        follow_redirects=False,
    )

    response = client.get("/api/auth/github/install", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "https://github.com/apps/pr-copilot/installations/new"

def test_install_redirects_anonymous_user_through_login(client):
    response = client.get("/api/auth/github/install", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/api/auth/github/login?next=install"


def test_login_can_continue_to_install_after_callback(client):
    login = client.get("/api/auth/github/login?next=install", follow_redirects=False)
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]

    callback = client.get(
        "/api/auth/github/callback",
        params={"code": "code-123", "state": state},
        follow_redirects=False,
    )

    assert callback.status_code == 302
    assert callback.headers["location"] == "/api/auth/github/install"


def test_install_oauth_callback_without_state_returns_to_product(client):
    login = client.get("/api/auth/github/login", follow_redirects=False)
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    client.get(
        "/api/auth/github/callback",
        params={"code": "code-123", "state": state},
        follow_redirects=False,
    )

    callback = client.get(
        "/api/auth/github/callback",
        params={"code": "install-code"},
        follow_redirects=False,
    )

    assert callback.status_code == 302
    assert callback.headers["location"] == "http://127.0.0.1:5173/?github_install=success"


def test_repository_status_reports_private_repo_installation(client):
    login = client.get("/api/auth/github/login", follow_redirects=False)
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    client.get(
        "/api/auth/github/callback",
        params={"code": "code-123", "state": state},
        follow_redirects=False,
    )

    response = client.get(
        "/api/auth/github/repositories/status",
        params={"owner": "octocat", "repo": "private-repo"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "repository": "octocat/private-repo",
        "authorized": True,
        "installation_id": 42,
        "install_url": "/api/auth/github/install",
    }


def test_setup_redirects_back_to_product(client):
    response = client.get("/api/auth/github/setup", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "http://127.0.0.1:5173/?github_install=success"


def test_login_unavailable_redirects_back_to_product():
    set_github_auth_service(_service(configured=False))
    app = FastAPI()
    app.include_router(router)
    try:
        response = TestClient(app).get("/api/auth/github/login", follow_redirects=False)
    finally:
        set_github_auth_service(None)

    assert response.status_code == 302
    assert response.headers["location"] == "http://127.0.0.1:5173/?github_auth=unavailable"


def test_install_unavailable_redirects_back_to_product():
    set_github_auth_service(_service(configured=False))
    app = FastAPI()
    app.include_router(router)
    try:
        response = TestClient(app).get("/api/auth/github/install", follow_redirects=False)
    finally:
        set_github_auth_service(None)

    assert response.status_code == 302
    assert response.headers["location"] == "http://127.0.0.1:5173/?github_install=unavailable"
