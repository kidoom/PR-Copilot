"""Tests for the memory session importer."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.agent.runtime.memory.models import (
    AgentKind,
    MemorySessionMeta,
)
from backend.agent.runtime.memory.store import FileMemoryStore
from backend.storage.pr_session.importer import ImportReport, import_memory_sessions
from backend.storage.pr_session.models import RunLifecycle
from backend.storage.pr_session.run_persistence import create_durable_run
from backend.storage.pr_session.store import FilePRSessionStore


@pytest.fixture
def storage_dir(tmp_path: Path):
    return tmp_path / "storage"


@pytest.fixture
def pr_store(storage_dir: Path):
    return FilePRSessionStore(storage_dir)


@pytest.fixture
def memory_store(storage_dir: Path):
    return FileMemoryStore(storage_dir)


def _create_memory_session(
    memory_store: FileMemoryStore,
    session_id: str,
    run_id: str,
    agent_kind: AgentKind = AgentKind.MAIN,
    agent_type: str = "main",
) -> MemorySessionMeta:
    meta = MemorySessionMeta(
        session_id=session_id,
        run_id=run_id,
        agent_kind=agent_kind,
        agent_type=agent_type,
        context_id="ctx_test",
    )
    return memory_store.create_session(meta)


class TestImportMemorySessions:
    def test_import_matched_sessions(self, pr_store, memory_store):
        """Sessions with matching run_id get imported."""
        # Create a durable run
        pr_meta, run_state = create_durable_run(
            pr_store,
            owner="acme",
            repo="widgets",
            pull_number=1,
            context_id="ctx_1",
            base_sha="aaa",
            head_sha="bbb",
        )
        run_id = run_id = run_state.run_id

        # Create memory sessions for this run
        _create_memory_session(memory_store, f"main_{run_id}", run_id)
        _create_memory_session(
            memory_store,
            f"sub_{run_id}",
            run_id,
            agent_kind=AgentKind.SUBAGENT,
            agent_type="security",
        )

        # Import
        report = import_memory_sessions(memory_store, pr_store)

        assert report.memory_sessions_scanned == 2
        assert report.sessions_with_run_id == 2
        assert report.runs_matched == 1
        assert report.runs_imported == 1
        assert report.sessions_unmatched == 0
        assert report.errors == []

        # Verify agent-sessions.json was created
        record = pr_store.load_agent_sessions(run_id)
        assert record is not None
        assert len(record.sessions) == 2
        assert record.run_id == run_id

        # Verify ref details
        main_ref = next(s for s in record.sessions if s.agent_kind == "main")
        assert main_ref.memory_session_id == f"main_{run_id}"
        assert main_ref.agent_type == "main"

        sub_ref = next(s for s in record.sessions if s.agent_kind == "subagent")
        assert sub_ref.memory_session_id == f"sub_{run_id}"
        assert sub_ref.agent_type == "security"

    def test_import_skips_already_imported(self, pr_store, memory_store):
        """Idempotent: skips runs that already have agent-sessions.json."""
        pr_meta, run_state = create_durable_run(
            pr_store,
            owner="acme",
            repo="widgets",
            pull_number=2,
            context_id="ctx_2",
            base_sha="aaa",
            head_sha="ccc",
        )
        run_id = run_state.run_id

        _create_memory_session(memory_store, f"main_{run_id}", run_id)

        # First import
        report1 = import_memory_sessions(memory_store, pr_store)
        assert report1.runs_imported == 1

        # Second import (idempotent)
        report2 = import_memory_sessions(memory_store, pr_store)
        assert report2.runs_matched == 1
        assert report2.runs_imported == 0  # Skipped

    def test_import_leaves_unmatched_alone(self, pr_store, memory_store):
        """Sessions without matching runs are counted as unmatched."""
        # Create a memory session with a non-existent run_id
        _create_memory_session(memory_store, "main_orphan", "run_nonexistent")

        report = import_memory_sessions(memory_store, pr_store)

        assert report.memory_sessions_scanned == 1
        assert report.sessions_with_run_id == 1
        assert report.runs_matched == 0
        assert report.runs_imported == 0
        assert report.sessions_unmatched == 1

        # No agent-sessions.json should exist
        assert pr_store.load_agent_sessions("run_nonexistent") is None

    def test_import_empty_run_id_skipped(self, pr_store, memory_store):
        """Sessions with empty run_id are counted as unmatched."""
        meta = MemorySessionMeta(
            session_id="empty_run",
            run_id="",
            agent_kind=AgentKind.MAIN,
            agent_type="main",
            context_id="ctx_test",
        )
        memory_store.create_session(meta)

        report = import_memory_sessions(memory_store, pr_store)

        assert report.memory_sessions_scanned == 1
        assert report.sessions_with_run_id == 0
        assert report.sessions_unmatched == 1

    def test_import_no_memory_sessions(self, pr_store, memory_store):
        """Empty memory store produces a clean report."""
        report = import_memory_sessions(memory_store, pr_store)

        assert report.memory_sessions_scanned == 0
        assert report.sessions_with_run_id == 0
        assert report.runs_matched == 0
        assert report.runs_imported == 0
        assert report.sessions_unmatched == 0
        assert report.errors == []

    def test_import_multiple_runs(self, pr_store, memory_store):
        """Multiple runs each with sessions get imported independently."""
        _, run1 = create_durable_run(
            pr_store, "acme", "widgets", 10, "ctx_a", "aaa", "bbb"
        )
        _, run2 = create_durable_run(
            pr_store, "acme", "widgets", 10, "ctx_b", "aaa", "ccc"
        )

        _create_memory_session(memory_store, f"m_{run1.run_id}", run1.run_id)
        _create_memory_session(memory_store, f"m_{run2.run_id}", run2.run_id)
        _create_memory_session(memory_store, "orphan", "run_missing")

        report = import_memory_sessions(memory_store, pr_store)

        assert report.memory_sessions_scanned == 3
        assert report.sessions_with_run_id == 3
        assert report.runs_matched == 2
        assert report.runs_imported == 2
        assert report.sessions_unmatched == 1

        # Both runs have records
        assert pr_store.load_agent_sessions(run1.run_id) is not None
        assert pr_store.load_agent_sessions(run2.run_id) is not None


class TestImportReport:
    def test_report_to_dict(self):
        report = ImportReport()
        report.memory_sessions_scanned = 5
        report.sessions_with_run_id = 4
        report.runs_matched = 2
        report.runs_imported = 2
        report.sessions_unmatched = 1

        d = report.to_dict()
        assert d["memory_sessions_scanned"] == 5
        assert d["sessions_with_run_id"] == 4
        assert d["runs_matched"] == 2
        assert d["runs_imported"] == 2
        assert d["sessions_unmatched"] == 1
        assert d["errors"] == []
