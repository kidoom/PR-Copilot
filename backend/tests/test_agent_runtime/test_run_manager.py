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


# --- Task 3.9: RunManager tests for queued, running, repeated, and already-terminal cancellation ---

def test_cancel_queued_run_transitions_directly_to_cancelled():
    """Cancelling a queued run goes directly to cancelled, not through cancelling."""
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")
    assert mgr.get_run("run-1").status == RunStatus.QUEUED
    mgr.cancel_run("run-1")
    session = mgr.get_run("run-1")
    assert session.status == RunStatus.CANCELLED
    assert session.cancelling is True


def test_cancel_running_run_sets_cancelling():
    """Cancelling an active run sets cancelling flag."""
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")
    mgr.mark_running("run-1")
    mgr.cancel_run("run-1")
    session = mgr.get_run("run-1")
    assert session.status == RunStatus.CANCELLING
    assert session.cancelling is True


def test_cancel_run_idempotent_repeated():
    """Repeated cancellation requests are idempotent."""
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")
    mgr.mark_running("run-1")
    mgr.cancel_run("run-1")
    mgr.cancel_run("run-1")  # second call
    session = mgr.get_run("run-1")
    assert session.status == RunStatus.CANCELLING
    assert session.cancelling is True


def test_cancel_already_cancelled_is_noop():
    """Cancelling an already-cancelled run is a no-op."""
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")
    mgr.mark_running("run-1")
    mgr.cancel_run("run-1")
    mgr.observe_cancellation("run-1")
    assert mgr.get_run("run-1").status == RunStatus.CANCELLED
    mgr.cancel_run("run-1")  # should be no-op
    assert mgr.get_run("run-1").status == RunStatus.CANCELLED


def test_cancel_completed_run_is_noop_preserves_completed():
    """Cancelling a completed run preserves completed state."""
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")
    mgr.complete_run("run-1", result={"output": "done"})
    mgr.cancel_run("run-1")
    assert mgr.get_run("run-1").status == RunStatus.COMPLETED


def test_cancel_failed_run_is_noop():
    """Cancelling a failed run preserves failed state."""
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")
    mgr.fail_run("run-1", error="broke")
    mgr.cancel_run("run-1")
    assert mgr.get_run("run-1").status == RunStatus.FAILED


# --- Task 3.10: Tests proving terminal events are published exactly once ---

def test_cancelled_terminal_event_published_exactly_once():
    """Only one run.cancelled event is published for a cancellation."""
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")
    mgr.mark_running("run-1")
    mgr.cancel_run("run-1")
    mgr.observe_cancellation("run-1")
    terminal = [e for e in mgr.get_retained_events("run-1") if e.type == RUN_CANCELLED]
    assert len(terminal) == 1


def test_queued_cancel_publishes_exactly_one_cancelled_event():
    """Cancelling a queued run publishes exactly one run.cancelled event."""
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")
    mgr.cancel_run("run-1")
    terminal = [e for e in mgr.get_retained_events("run-1") if e.type == RUN_CANCELLED]
    assert len(terminal) == 1


def test_completed_terminal_event_not_overwritten_by_late_cancel():
    """Completion event is preserved when cancel arrives after completion."""
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")
    mgr.complete_run("run-1", result={"output": "ok"})
    mgr.cancel_run("run-1")  # should be no-op
    events = mgr.get_retained_events("run-1")
    completed = [e for e in events if e.type == RUN_COMPLETED]
    cancelled = [e for e in events if e.type == RUN_CANCELLED]
    assert len(completed) == 1
    assert len(cancelled) == 0


def test_failed_terminal_event_not_overwritten_by_late_cancel():
    """Failure event is preserved when cancel arrives after failure."""
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")
    mgr.fail_run("run-1", error="broke")
    mgr.cancel_run("run-1")  # should be no-op
    events = mgr.get_retained_events("run-1")
    failed = [e for e in events if e.type == RUN_FAILED]
    cancelled = [e for e in events if e.type == RUN_CANCELLED]
    assert len(failed) == 1
    assert len(cancelled) == 0


def test_complete_run_prevents_overwrite_after_cancel():
    """complete_run is no-op when run is already cancelled."""
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")
    mgr.mark_running("run-1")
    mgr.cancel_run("run-1")
    mgr.observe_cancellation("run-1")
    mgr.complete_run("run-1", result={"output": "late"})  # should be no-op
    assert mgr.get_run("run-1").status == RunStatus.CANCELLED


def test_fail_run_prevents_overwrite_after_cancel():
    """fail_run is no-op when run is already cancelled."""
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")
    mgr.mark_running("run-1")
    mgr.cancel_run("run-1")
    mgr.observe_cancellation("run-1")
    mgr.fail_run("run-1", error="late error")  # should be no-op
    assert mgr.get_run("run-1").status == RunStatus.CANCELLED


# --- Task 3.1: Background execution task registration ---

def test_register_and_get_execution_task():
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")

    async def _dummy():
        pass

    task = asyncio.ensure_future(_dummy())
    mgr.register_execution_task("run-1", task)
    assert mgr.get_execution_task("run-1") is task
    task.cancel()


def test_get_execution_task_returns_none_when_not_registered():
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")
    assert mgr.get_execution_task("run-1") is None


def test_execution_task_cleaned_up_on_complete():
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")

    async def _dummy():
        pass

    task = asyncio.ensure_future(_dummy())
    mgr.register_execution_task("run-1", task)
    mgr.complete_run("run-1")
    assert mgr.get_execution_task("run-1") is None
    task.cancel()


def test_execution_task_cleaned_up_on_cancel():
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")
    mgr.mark_running("run-1")

    async def _dummy():
        await asyncio.sleep(10)

    task = asyncio.ensure_future(_dummy())
    mgr.register_execution_task("run-1", task)
    mgr.cancel_run("run-1")
    # Task should be cancelled and reference cleaned up after observe
    mgr.observe_cancellation("run-1")
    assert mgr.get_execution_task("run-1") is None


def test_mark_running_noop_on_terminal():
    """mark_running is no-op on terminal states."""
    mgr = RunManager()
    mgr.create_run("ctx-1", run_id="run-1")
    mgr.complete_run("run-1")
    mgr.mark_running("run-1")
    assert mgr.get_run("run-1").status == RunStatus.COMPLETED
