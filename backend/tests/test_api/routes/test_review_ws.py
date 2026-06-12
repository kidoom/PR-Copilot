from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.agent.runtime.events import RUN_STARTED, RUN_COMPLETED
from backend.api.routes.review_ws import router


def _make_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


def test_websocket_rejects_unknown_run():
    app = _make_test_app()
    client = TestClient(app)

    with pytest.raises(Exception):
        with client.websocket_connect("/ws/review-runs/nonexistent"):
            pass


def test_websocket_streams_events_for_existing_run():
    from backend.api.routes.review_runs import _run_manager
    run = _run_manager.create_run("ctx-1", run_id="ws-test-1")
    _run_manager.mark_running("ws-test-1")

    app = _make_test_app()
    client = TestClient(app)

    with client.websocket_connect("/ws/review-runs/ws-test-1") as ws:
        _run_manager.publish_event("ws-test-1", RUN_STARTED, {"msg": "started"})
        data = ws.receive_json()
        assert data["type"] == RUN_STARTED
        assert data["run_id"] == "ws-test-1"
        assert data["payload"]["msg"] == "started"
        assert "sequence" in data
        assert "event_id" in data
        assert "created_at" in data


def test_websocket_replays_retained_events():
    from backend.api.routes.review_runs import _run_manager
    run = _run_manager.create_run("ctx-1", run_id="ws-test-replay")
    _run_manager.mark_running("ws-test-replay")

    _run_manager.publish_event("ws-test-replay", RUN_STARTED, {"i": 0})
    _run_manager.publish_event("ws-test-replay", "message.delta", {"i": 1})

    app = _make_test_app()
    client = TestClient(app)

    with client.websocket_connect("/ws/review-runs/ws-test-replay") as ws:
        data1 = ws.receive_json()
        assert data1["type"] == RUN_STARTED
        assert data1["sequence"] == 0

        data2 = ws.receive_json()
        assert data2["type"] == "message.delta"
        assert data2["sequence"] == 1


def test_websocket_streams_live_events_after_replay():
    from backend.api.routes.review_runs import _run_manager
    run = _run_manager.create_run("ctx-1", run_id="ws-test-live")
    _run_manager.mark_running("ws-test-live")

    _run_manager.publish_event("ws-test-live", RUN_STARTED, {"i": 0})

    app = _make_test_app()
    client = TestClient(app)

    with client.websocket_connect("/ws/review-runs/ws-test-live") as ws:
        data1 = ws.receive_json()
        assert data1["type"] == RUN_STARTED

        _run_manager.publish_event("ws-test-live", "message.delta", {"text": "hello"})
        data2 = ws.receive_json()
        assert data2["type"] == "message.delta"
        assert data2["payload"]["text"] == "hello"


def test_websocket_closes_on_terminal_event():
    from backend.api.routes.review_runs import _run_manager
    run = _run_manager.create_run("ctx-1", run_id="ws-test-terminal")
    _run_manager.mark_running("ws-test-terminal")

    app = _make_test_app()
    client = TestClient(app)

    with client.websocket_connect("/ws/review-runs/ws-test-terminal") as ws:
        _run_manager.complete_run("ws-test-terminal", result={"output": "done"})
        data = ws.receive_json()
        assert data["type"] == RUN_COMPLETED


def test_websocket_closes_immediately_for_finished_run():
    from backend.api.routes.review_runs import _run_manager
    run = _run_manager.create_run("ctx-1", run_id="ws-test-finished")
    _run_manager.mark_running("ws-test-finished")
    _run_manager.complete_run("ws-test-finished", result={"output": "done"})

    app = _make_test_app()
    client = TestClient(app)

    with client.websocket_connect("/ws/review-runs/ws-test-finished") as ws:
        data = ws.receive_json()
        assert data["type"] == RUN_COMPLETED
