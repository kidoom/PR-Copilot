from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import app


def test_health_endpoint_works_without_auth():
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200


def test_pr_endpoint_accessible_without_login():
    """No login middleware — endpoints are accessible directly."""
    response = TestClient(app).get("/api/pr/context/missing/patch-index")
    # Should get 404 (context not found), not 401 (login required)
    assert response.status_code == 404
    assert response.json()["detail"] == "Context not found"

