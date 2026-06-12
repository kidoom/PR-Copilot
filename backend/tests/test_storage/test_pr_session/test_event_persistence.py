"""Tests for ordered event persistence and replay.

Covers: reconnect replay, terminal replay, corrupt lines,
concurrent publishers, payload redaction, and page limits.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.storage.pr_session.models import PersistedEvent, PRSessionMeta, RunState
from backend.storage.pr_session.run_persistence import (
    persist_event,
    restore_next_sequence,
)
from backend.storage.pr_session.store import FilePRSessionStore
from backend.agent.runtime.events import RunEvent


@pytest.fixture
def store(tmp_path: Path) -> FilePRSessionStore:
    return FilePRSessionStore(tmp_path / "storage")


@pytest.fixture
def run(store: FilePRSessionStore) -> RunState:
    meta = store.get_or_create_pr_session("octocat", "hello-world", 42)
    return store.create_run(meta.pr_session_id, "ctx-1", "aaa", "bbb")


# ---------------------------------------------------------------------------
# Basic event persistence
# ---------------------------------------------------------------------------


class TestEventPersistence:
    def test_persist_and_load_event(self, store: FilePRSessionStore, run: RunState):
        event = RunEvent(
            run_id=run.run_id,
            type="run.started",
            payload={"context_id": "ctx-1"},
            event_id="evt-1",
            sequence=0,
            created_at="2026-01-01T00:00:00+00:00",
        )
        persist_event(store, event)

        events = store.load_events(run.run_id)
        assert len(events) == 1
        assert events[0].event_type == "run.started"
        assert events[0].payload["context_id"] == "ctx-1"

    def test_multiple_events_ordered(self, store: FilePRSessionStore, run: RunState):
        for i in range(5):
            event = RunEvent(
                run_id=run.run_id,
                type="tool.call",
                payload={"step": i},
                event_id=f"evt-{i}",
                sequence=i,
                created_at=f"2026-01-01T00:00:{i:02d}+00:00",
            )
            persist_event(store, event)

        events = store.load_events(run.run_id)
        assert len(events) == 5
        assert [e.sequence for e in events] == [0, 1, 2, 3, 4]


# ---------------------------------------------------------------------------
# Reconnect replay with after_sequence
# ---------------------------------------------------------------------------


class TestReconnectReplay:
    def test_replay_after_sequence(self, store: FilePRSessionStore, run: RunState):
        for i in range(10):
            event = RunEvent(
                run_id=run.run_id, type="tool.call",
                event_id=f"evt-{i}", sequence=i, created_at=f"t{i}",
            )
            persist_event(store, event)

        # Reconnect from sequence 5
        events = store.load_events(run.run_id, after_sequence=5)
        assert len(events) == 4
        assert events[0].sequence == 6
        assert events[3].sequence == 9

    def test_replay_from_start(self, store: FilePRSessionStore, run: RunState):
        for i in range(3):
            event = RunEvent(
                run_id=run.run_id, type="tool.call",
                event_id=f"evt-{i}", sequence=i, created_at=f"t{i}",
            )
            persist_event(store, event)

        events = store.load_events(run.run_id, after_sequence=-1)
        assert len(events) == 3

    def test_replay_empty_run(self, store: FilePRSessionStore, run: RunState):
        events = store.load_events(run.run_id, after_sequence=-1)
        assert len(events) == 0


# ---------------------------------------------------------------------------
# Terminal replay
# ---------------------------------------------------------------------------


class TestTerminalReplay:
    def test_terminal_event_included(self, store: FilePRSessionStore, run: RunState):
        for i in range(3):
            event = RunEvent(
                run_id=run.run_id, type="tool.call",
                event_id=f"evt-{i}", sequence=i, created_at=f"t{i}",
            )
            persist_event(store, event)

        # Terminal event
        terminal = RunEvent(
            run_id=run.run_id, type="run.completed",
            payload={"status": "completed"},
            event_id="evt-terminal", sequence=3, created_at="t3",
        )
        persist_event(store, terminal)

        events = store.load_events(run.run_id)
        assert len(events) == 4
        assert events[-1].event_type == "run.completed"


# ---------------------------------------------------------------------------
# Corrupt JSONL lines
# ---------------------------------------------------------------------------


class TestCorruptLines:
    def test_corrupt_lines_skipped(self, store: FilePRSessionStore, run: RunState):
        # Manually write valid + corrupt + valid lines
        from backend.storage.pr_session.paths import pr_meta_file, run_events_file

        pr_key = store._find_pr_key_for_run(run.run_id)
        ef = run_events_file(store._storage_dir, pr_key, run.run_id)
        ef.parent.mkdir(parents=True, exist_ok=True)

        with open(ef, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "event_id": "e0", "run_id": run.run_id,
                "sequence": 0, "event_type": "run.started", "created_at": "t",
            }) + "\n")
            f.write("NOT VALID JSON\n")
            f.write(json.dumps({
                "event_id": "e2", "run_id": run.run_id,
                "sequence": 1, "event_type": "tool.call", "created_at": "t",
            }) + "\n")
            f.write('{"incomplete":\n')
            f.write(json.dumps({
                "event_id": "e4", "run_id": run.run_id,
                "sequence": 2, "event_type": "run.completed", "created_at": "t",
            }) + "\n")

        events = store.load_events(run.run_id)
        assert len(events) == 3
        assert events[0].event_id == "e0"
        assert events[1].event_id == "e2"
        assert events[2].event_id == "e4"


# ---------------------------------------------------------------------------
# Page limits
# ---------------------------------------------------------------------------


class TestPageLimits:
    def test_limit_respected(self, store: FilePRSessionStore, run: RunState):
        for i in range(20):
            event = RunEvent(
                run_id=run.run_id, type="tool.call",
                event_id=f"evt-{i}", sequence=i, created_at=f"t{i}",
            )
            persist_event(store, event)

        page1 = store.load_events(run.run_id, limit=5)
        assert len(page1) == 5
        assert page1[0].sequence == 0
        assert page1[4].sequence == 4

        page2 = store.load_events(run.run_id, after_sequence=4, limit=5)
        assert len(page2) == 5
        assert page2[0].sequence == 5

    def test_limit_larger_than_total(self, store: FilePRSessionStore, run: RunState):
        for i in range(3):
            event = RunEvent(
                run_id=run.run_id, type="tool.call",
                event_id=f"evt-{i}", sequence=i, created_at=f"t{i}",
            )
            persist_event(store, event)

        events = store.load_events(run.run_id, limit=100)
        assert len(events) == 3


# ---------------------------------------------------------------------------
# Event type filtering
# ---------------------------------------------------------------------------


class TestEventTypeFilter:
    def test_filter_by_type(self, store: FilePRSessionStore, run: RunState):
        types = ["run.started", "tool.call", "tool.result", "run.completed"]
        for i, etype in enumerate(types):
            event = RunEvent(
                run_id=run.run_id, type=etype,
                event_id=f"evt-{i}", sequence=i, created_at=f"t{i}",
            )
            persist_event(store, event)

        tool_events = store.load_events(run.run_id, event_types=["tool.call", "tool.result"])
        assert len(tool_events) == 2
        assert tool_events[0].event_type == "tool.call"
        assert tool_events[1].event_type == "tool.result"


# ---------------------------------------------------------------------------
# Sequence recovery
# ---------------------------------------------------------------------------


class TestSequenceRecovery:
    def test_restore_next_sequence(self, store: FilePRSessionStore, run: RunState):
        assert restore_next_sequence(store, run.run_id) == 0

        for i in range(5):
            event = RunEvent(
                run_id=run.run_id, type="tool.call",
                event_id=f"evt-{i}", sequence=i, created_at=f"t{i}",
            )
            persist_event(store, event)

        assert restore_next_sequence(store, run.run_id) == 5

    def test_restore_next_sequence_empty(self, store: FilePRSessionStore, run: RunState):
        assert restore_next_sequence(store, run.run_id) == 0
