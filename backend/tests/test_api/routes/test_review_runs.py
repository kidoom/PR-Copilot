from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.agent.model.client import ModelClient
from backend.agent.model.messages import Message, ModelResponse, Role, ToolUseBlock
from backend.agent.runtime.run_manager import RunManager
from backend.api.routes.review_runs import router
from backend.deps import create_agent_deps, set_agent_deps
from backend.domain.pr_context.context_manager import (
    PRContext,
    _contexts,
    DerivedSignals,
)
from backend.domain.pr_context.fetcher import PRMetadata, CommitsData


def _make_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


def _seed_context(context_id: str) -> None:
    ctx = PRContext(
        context_id=context_id,
        source="github",
        fetched_at="2026-01-01T00:00:00+00:00",
        cache_key="owner/repo/1/abc",
        owner="owner",
        repo="repo",
        pull_number=1,
        pr=PRMetadata(
            title="Test PR",
            body="",
            author="user",
            url="https://github.com/owner/repo/pull/1",
            state="open",
            merged=False,
            base_branch="main",
            head_branch="feature",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            additions=10,
            deletions=5,
            changed_files=2,
        ),
        commits=CommitsData(head_sha="abc123", commits=[]),
        files=[],
        derived=DerivedSignals(
            total_hunks=0,
            source_files_changed=0,
            test_files_changed=0,
            docs_only=False,
            has_source_without_tests=False,
        ),
    )
    _contexts[context_id] = ctx


def _unseed_context(context_id: str) -> None:
    _contexts.pop(context_id, None)


class FakeModel(ModelClient):
    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self._call_count = 0

    async def chat(self, messages: list[Message], tool_schemas=None) -> ModelResponse:
        resp = self._responses[self._call_count]
        self._call_count += 1
        return resp


@pytest.mark.asyncio
async def test_create_run_returns_run_id_and_status(monkeypatch):
    import os
    os.environ.setdefault("OPENAI_API_KEY", "test-key")

    _seed_context("ctx-test-1")

    try:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        deps = create_agent_deps()
        deps.new_model = lambda: FakeModel([ModelResponse(content="done", tool_use_blocks=[])])
        set_agent_deps(deps)

        app = _make_test_app()
        client = TestClient(app)

        resp = client.post("/api/review/runs", json={"context_id": "ctx-test-1"})
        assert resp.status_code == 200
        data = resp.json()
        assert "run_id" in data
        assert data["status"] == "queued"
    finally:
        _unseed_context("ctx-test-1")
        set_agent_deps(None)


def test_create_run_rejects_unknown_context():
    app = _make_test_app()
    client = TestClient(app)

    resp = client.post("/api/review/runs", json={"context_id": "nonexistent"})
    assert resp.status_code == 404


def test_get_run_status():
    from backend.api.routes.review_runs import _run_manager
    _run_manager.create_run("ctx-1", run_id="run-test-1")

    app = _make_test_app()
    client = TestClient(app)

    resp = client.get("/api/review/runs/run-test-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == "run-test-1"
    assert data["status"] == "queued"


def test_get_run_status_unknown():
    app = _make_test_app()
    client = TestClient(app)

    resp = client.get("/api/review/runs/nonexistent")
    assert resp.status_code == 404


def test_cancel_run():
    from backend.api.routes.review_runs import _run_manager
    _run_manager.create_run("ctx-1", run_id="run-test-2")
    _run_manager.mark_running("run-test-2")

    app = _make_test_app()
    client = TestClient(app)

    resp = client.post("/api/review/runs/run-test-2/cancel")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == "run-test-2"
    assert data["status"] == "cancelling"


def test_cancel_run_unknown():
    app = _make_test_app()
    client = TestClient(app)

    resp = client.post("/api/review/runs/nonexistent/cancel")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_run_does_not_block(monkeypatch):
    import os
    import asyncio
    os.environ.setdefault("OPENAI_API_KEY", "test-key")

    _seed_context("ctx-test-block")

    try:
        deps = create_agent_deps()

        class SlowModel(ModelClient):
            async def chat(self, messages, tool_schemas=None):
                await asyncio.sleep(0.1)
                return ModelResponse(content="done", tool_use_blocks=[])

        deps.new_model = lambda: SlowModel()
        set_agent_deps(deps)

        app = _make_test_app()
        client = TestClient(app)

        resp = client.post("/api/review/runs", json={"context_id": "ctx-test-block"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "queued"
        assert "run_id" in data
    finally:
        _unseed_context("ctx-test-block")
        set_agent_deps(None)


def test_create_run_passes_local_credential_to_background_task(monkeypatch):
    from backend.domain.github.local_credentials import CredentialSource, ResolvedCredential

    captured: dict[str, Any] = {}

    async def fake_execute_run(run_id, context_id, task_plan, local_repo_root, token):
        captured["token"] = token

    _seed_context("ctx-test-auth")
    try:
        monkeypatch.setattr(
            "backend.api.routes.review_runs.resolve_github_token",
            lambda: ResolvedCredential(token="ghp_local", source=CredentialSource.GH_TOKEN),
        )
        monkeypatch.setattr(
            "backend.api.routes.review_runs._execute_run",
            fake_execute_run,
        )
        app = _make_test_app()

        response = TestClient(app).post(
            "/api/review/runs",
            json={"context_id": "ctx-test-auth"},
        )

        assert response.status_code == 200
        assert captured["token"] == "ghp_local"
    finally:
        _unseed_context("ctx-test-auth")
