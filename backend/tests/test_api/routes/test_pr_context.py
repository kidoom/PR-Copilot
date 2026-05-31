from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import pr_context
from backend.domain.github.client import GitHubAPIError


class FakeGitHubClient:
    tokens: list[str | None] = []

    def __init__(self, token=None):
        self.tokens.append(token)

    async def get_pr(self, owner, repo, pull_number):
        return {
            "title": "Test PR",
            "body": "",
            "user": {"login": "octocat"},
            "html_url": f"https://github.com/{owner}/{repo}/pull/{pull_number}",
            "state": "open",
            "merged": False,
            "base": {"ref": "main"},
            "head": {"ref": "feature"},
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "additions": 0,
            "deletions": 0,
            "changed_files": 0,
        }

    async def get_commits(self, owner, repo, pull_number):
        return []

    async def get_files(self, owner, repo, pull_number):
        return []

    async def close(self):
        pass


def test_create_context_prefers_authenticated_session_token(monkeypatch):
    async def authenticated_session(request):
        return SimpleNamespace(session_id="session-1", access_token="ghu_session")

    FakeGitHubClient.tokens = []
    monkeypatch.setattr(pr_context, "get_authenticated_github_session", authenticated_session)
    monkeypatch.setattr(pr_context, "GitHubClient", FakeGitHubClient)
    app = FastAPI()
    app.include_router(pr_context.router)

    response = TestClient(app).post(
        "/api/pr/context",
        json={
            "pr_url": "https://github.com/owner/repo/pull/1",
            "github_token": "legacy-token",
        },
    )

    assert response.status_code == 200
    assert FakeGitHubClient.tokens == ["ghu_session"]


def test_create_context_prompts_to_connect_private_repository(monkeypatch):
    class InaccessibleGitHubClient(FakeGitHubClient):
        async def get_pr(self, owner, repo, pull_number):
            raise GitHubAPIError(404, "PR not found or repository is private", "not_found")

    class AuthService:
        async def repository_access(self, session_id, *, owner, repo):
            return {"authorized": False, "installation_id": None}

    async def authenticated_session(request):
        return SimpleNamespace(session_id="session-1", access_token="ghu_session")

    monkeypatch.setattr(pr_context, "get_authenticated_github_session", authenticated_session)
    monkeypatch.setattr(pr_context, "get_github_auth_service", lambda: AuthService())
    monkeypatch.setattr(pr_context, "GitHubClient", InaccessibleGitHubClient)
    app = FastAPI()
    app.include_router(pr_context.router)

    response = TestClient(app).post(
        "/api/pr/context",
        json={"pr_url": "https://github.com/owner/private-repo/pull/1"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == {
        "code": "github_app_repository_access_required",
        "message": "Connect this repository to the GitHub App, then try the PR analysis again.",
        "install_url": "/api/auth/github/install",
    }
