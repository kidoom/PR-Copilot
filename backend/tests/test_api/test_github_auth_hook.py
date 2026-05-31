from __future__ import annotations

from dataclasses import dataclass

from fastapi.testclient import TestClient

from backend.domain.github.auth import set_github_auth_service
from backend.main import app


@dataclass
class FakeConfig:
    cookie_name: str = "pr_copilot_session"
    configured: bool = True


class FakeAuthService:
    config = FakeConfig()

    async def get_session(self, session_id):
        return object() if session_id == "valid-session" else None


def test_hook_allows_health_without_github_login():
    set_github_auth_service(FakeAuthService())
    try:
        response = TestClient(app).get("/api/health")
    finally:
        set_github_auth_service(None)

    assert response.status_code == 200


def test_hook_rejects_anonymous_business_api_request():
    set_github_auth_service(FakeAuthService())
    try:
        response = TestClient(app).get("/api/pr/context/missing/patch-index")
    finally:
        set_github_auth_service(None)

    assert response.status_code == 401
    assert response.json() == {"detail": "GitHub login required"}


def test_hook_allows_authenticated_business_api_request():
    set_github_auth_service(FakeAuthService())
    try:
        response = TestClient(app).get(
            "/api/pr/context/missing/patch-index",
            cookies={"pr_copilot_session": "valid-session"},
        )
    finally:
        set_github_auth_service(None)

    assert response.status_code == 404
    assert response.json() == {"detail": "Context not found"}

