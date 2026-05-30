from __future__ import annotations

import pytest

from backend.agent.runtime.events import (
    AgentRunSession,
    RunEvent,
    RunStatus,
    RUN_STARTED,
    RUN_COMPLETED,
    RUN_CANCELLED,
    SUPPORTED_EVENT_TYPES,
)


def test_run_event_serialization_includes_required_fields():
    event = RunEvent(run_id="run-1", type=RUN_STARTED, payload={"key": "value"})
    d = event.to_dict()

    assert "event_id" in d
    assert d["run_id"] == "run-1"
    assert d["type"] == RUN_STARTED
    assert "sequence" in d
    assert "created_at" in d
    assert d["payload"] == {"key": "value"}


def test_run_event_has_unique_event_id():
    e1 = RunEvent(run_id="run-1", type=RUN_STARTED)
    e2 = RunEvent(run_id="run-1", type=RUN_STARTED)
    assert e1.event_id != e2.event_id


def test_run_event_type_in_supported_set():
    for etype in [RUN_STARTED, RUN_COMPLETED, RUN_CANCELLED]:
        assert etype in SUPPORTED_EVENT_TYPES


def test_run_status_enum_values():
    assert RunStatus.QUEUED.value == "queued"
    assert RunStatus.RUNNING.value == "running"
    assert RunStatus.CANCELLING.value == "cancelling"
    assert RunStatus.CANCELLED.value == "cancelled"
    assert RunStatus.COMPLETED.value == "completed"
    assert RunStatus.FAILED.value == "failed"


def test_agent_run_session_default_state():
    session = AgentRunSession(run_id="run-1", context_id="ctx-1")
    assert session.status == RunStatus.QUEUED
    assert session.retained_events == []
    assert session.subscriber_queues == []
    assert session.cancelling is False
    assert session.final_result is None
    assert session.error_summary is None


def test_agent_run_session_allocate_sequence_is_monotonic():
    session = AgentRunSession(run_id="run-1", context_id="ctx-1")
    seqs = [session.allocate_sequence() for _ in range(5)]
    assert seqs == [0, 1, 2, 3, 4]


def test_event_sequence_values_increase_monotonically():
    session = AgentRunSession(run_id="run-1", context_id="ctx-1")
    events = []
    for _ in range(10):
        seq = session.allocate_sequence()
        event = RunEvent(run_id="run-1", type=RUN_STARTED, sequence=seq)
        events.append(event)

    sequences = [e.sequence for e in events]
    assert sequences == sorted(sequences)
    assert len(set(sequences)) == len(sequences)


def test_run_event_to_dict_roundtrip():
    event = RunEvent(
        run_id="run-abc",
        type=RUN_COMPLETED,
        payload={"output": "done"},
        event_id="evt-1",
        sequence=42,
        created_at="2026-01-01T00:00:00+00:00",
    )
    d = event.to_dict()
    assert d["event_id"] == "evt-1"
    assert d["run_id"] == "run-abc"
    assert d["type"] == RUN_COMPLETED
    assert d["sequence"] == 42
    assert d["created_at"] == "2026-01-01T00:00:00+00:00"
    assert d["payload"] == {"output": "done"}
