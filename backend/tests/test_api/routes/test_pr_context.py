from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import pr_context
from backend.domain.github.client import GitHubAPIError
from backend.domain.github.local_credentials import CredentialSource, ResolvedCredential


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


def test_create_context_uses_local_credential(monkeypatch):
    FakeGitHubClient.tokens = []
    monkeypatch.setattr(
        pr_context, "resolve_github_token",
        lambda: ResolvedCredential(token="ghp_local", source=CredentialSource.GH_TOKEN),
    )
    monkeypatch.setattr(pr_context, "GitHubClient", FakeGitHubClient)
    app = FastAPI()
    app.include_router(pr_context.router)

    response = TestClient(app).post(
        "/api/pr/context",
        json={"pr_url": "https://github.com/owner/repo/pull/1"},
    )

    assert response.status_code == 200
    assert FakeGitHubClient.tokens == ["ghp_local"]


def test_create_context_returns_auth_guidance_for_private_repo(monkeypatch):
    class InaccessibleGitHubClient(FakeGitHubClient):
        async def get_pr(self, owner, repo, pull_number):
            raise GitHubAPIError(404, "PR not found or repository is private", "not_found")

    monkeypatch.setattr(
        pr_context, "resolve_github_token",
        lambda: ResolvedCredential(token="", source=CredentialSource.ANONYMOUS),
    )
    monkeypatch.setattr(pr_context, "GitHubClient", InaccessibleGitHubClient)
    app = FastAPI()
    app.include_router(pr_context.router)

    response = TestClient(app).post(
        "/api/pr/context",
        json={"pr_url": "https://github.com/owner/private-repo/pull/1"},
    )

    assert response.status_code == 404
    assert "PR not found" in response.json()["detail"]
