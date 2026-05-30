from __future__ import annotations

import asyncio
import pytest

from backend.agent.runtime.events import RunStatus, RUN_STARTED, RUN_COMPLETED, RUN_CANCELLED, RUN_FAILED
from backend.agent.runtime.run_manager import RunManager, RunNotFoundError


def test_create_run_returns_session_with_unique_id():
    mgr = RunManager()
    s1 = mgr.create_run("ctx-1")
    s2 = mgr.create_run("ctx-1")
    assert s1.run_id != s2.run_id
    assert s1.context_id == "ctx-1"
    assert s1.status == RunStatus.QUEUED


def test_create_run_with_explicit_id():
    mgr = RunManager()
    session = mgr.create_run("ctx-1", run_id="custom-id")
    assert session.run_id == "custom-id"
    assert mgr.has_run("custom-id")


def test_get_run_raises_for_unknown():
    mgr = RunManager()
    with pytest.raises(RunNotFoundError):
        mgr.get_run("nonexistent")


def test_has_run():
    mgr = RunManager()
    assert not mgr.has_run("run-1")
    mgr.create_run("ctx-1", run_id="run-1")
    assert mgr.has_run("run-1")


def test_publish_event_stores_in_retained():
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")
    event = mgr.publish_event("run-1", RUN_STARTED, {"key": "value"})
    assert event.run_id == "run-1"
    assert event.type == RUN_STARTED
    assert event.sequence == 0
    retained = mgr.get_retained_events("run-1")
    assert len(retained) == 1
    assert retained[0].event_id == event.event_id


def test_publish_event_increments_sequence():
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")
    e1 = mgr.publish_event("run-1", RUN_STARTED)
    e2 = mgr.publish_event("run-1", "message.delta")
    e3 = mgr.publish_event("run-1", RUN_COMPLETED)
    assert e1.sequence == 0
    assert e2.sequence == 1
    assert e3.sequence == 2


def test_retained_events_capped_at_max():
    mgr = RunManager(max_retained_events=3)
    mgr.create_run("ctx-1", run_id="run-1")
    for i in range(5):
        mgr.publish_event("run-1", "message.delta", {"i": i})
    retained = mgr.get_retained_events("run-1")
    assert len(retained) == 3
    assert retained[0].payload["i"] == 2
    assert retained[2].payload["i"] == 4


@pytest.mark.asyncio
async def test_subscribe_receives_live_events():
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")
    queue = mgr.subscribe("run-1")
    mgr.publish_event("run-1", RUN_STARTED, {"msg": "started"})
    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event.type == RUN_STARTED
    assert event.payload["msg"] == "started"


@pytest.mark.asyncio
async def test_replay_retained_events_for_late_subscriber():
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")
    mgr.publish_event("run-1", RUN_STARTED)
    mgr.publish_event("run-1", "message.delta", {"text": "hello"})

    retained = mgr.get_retained_events("run-1")
    assert len(retained) == 2

    queue = mgr.subscribe("run-1")
    for event in retained:
        queue.put_nowait(event)
    mgr.publish_event("run-1", RUN_COMPLETED)

    received = []
    while not queue.empty():
        received.append(await queue.get())
    assert len(received) == 3
    assert received[0].type == RUN_STARTED
    assert received[1].type == "message.delta"
    assert received[2].type == RUN_COMPLETED


def test_get_status_returns_current_state():
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")
    status = mgr.get_status("run-1")
    assert status["run_id"] == "run-1"
    assert status["context_id"] == "ctx-1"
    assert status["status"] == "queued"


def test_get_status_includes_final_result():
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")
    mgr.complete_run("run-1", result={"output": "done"})
    status = mgr.get_status("run-1")
    assert status["status"] == "completed"
    assert status["final_result"]["output"] == "done"


def test_get_status_includes_error_summary():
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")
    mgr.fail_run("run-1", error="something broke")
    status = mgr.get_status("run-1")
    assert status["status"] == "failed"
    assert status["error_summary"] == "something broke"


def test_mark_running():
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")
    mgr.mark_running("run-1")
    assert mgr.get_run("run-1").status == RunStatus.RUNNING


def test_complete_run():
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")
    mgr.complete_run("run-1", result={"output": "ok"})
    session = mgr.get_run("run-1")
    assert session.status == RunStatus.COMPLETED
    assert session.final_result == {"output": "ok"}


def test_fail_run():
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")
    mgr.fail_run("run-1", error="oops")
    session = mgr.get_run("run-1")
    assert session.status == RunStatus.FAILED
    assert session.error_summary == "oops"


def test_cancel_run_transitions_to_cancelling():
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")
    mgr.mark_running("run-1")
    mgr.cancel_run("run-1")
    session = mgr.get_run("run-1")
    assert session.status == RunStatus.CANCELLING
    assert session.cancelling is True


def test_cancel_completed_run_is_noop():
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")
    mgr.complete_run("run-1")
    mgr.cancel_run("run-1")
    assert mgr.get_run("run-1").status == RunStatus.COMPLETED


def test_observe_cancellation_publishes_terminal_event():
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")
    mgr.mark_running("run-1")
    mgr.cancel_run("run-1")
    mgr.observe_cancellation("run-1")
    session = mgr.get_run("run-1")
    assert session.status == RunStatus.CANCELLED
    terminal = [e for e in session.retained_events if e.type == RUN_CANCELLED]
    assert len(terminal) == 1


def test_observe_cancellation_noop_if_not_cancelling():
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")
    mgr.mark_running("run-1")
    mgr.observe_cancellation("run-1")
    assert mgr.get_run("run-1").status == RunStatus.RUNNING


def test_is_cancelling():
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")
    assert not mgr.is_cancelling("run-1")
    mgr.cancel_run("run-1")
    assert mgr.is_cancelling("run-1")


def test_is_cancelling_unknown_run():
    mgr = RunManager()
    assert not mgr.is_cancelling("nonexistent")
