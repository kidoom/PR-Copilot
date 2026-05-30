from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.agent.runtime.memory.models import (
    AgentKind,
    EntryType,
    MemorySessionMeta,
    TranscriptEntry,
)
from backend.agent.runtime.memory.session_id import validate_session_id


class MemoryStoreError(Exception):
    """Base exception for memory store operations."""


class SessionNotFoundError(MemoryStoreError):
    """Raised when a session is not found."""


class SessionAlreadyExistsError(MemoryStoreError):
    """Raised when trying to create a session that already exists."""


class InvalidSessionError(MemoryStoreError):
    """Raised when session metadata is invalid."""


class AppendToUnknownSessionError(MemoryStoreError):
    """Raised when trying to append to a non-existent session."""


def _normalize_agent_type(agent_type: str) -> str:
    """Normalize agent_type for safe use in filesystem paths.

    - Lowercase
    - Replace non-alphanumeric chars (except hyphen) with hyphen
    - Collapse multiple hyphens
    - Strip leading/trailing hyphens
    """
    normalized = re.sub(r"[^a-z0-9-]", "-", agent_type.lower())
    normalized = re.sub(r"-+", "-", normalized)
    return normalized.strip("-")


def _validate_path_is_under(path: Path, parent: Path) -> bool:
    """Check that resolved path is under parent directory."""
    try:
        resolved = path.resolve()
        parent_resolved = parent.resolve()
        resolved.relative_to(parent_resolved)
        return True
    except ValueError:
        return False


class FileMemoryStore:
    """File-backed memory store for agent sessions.

    Stores sessions as directories with:
    - state.json: session metadata and counters
    - transcript.jsonl: append-only transcript entries
    """

    def __init__(self, storage_dir: str | Path):
        self._storage_dir = Path(storage_dir)
        self._memory_dir = self._storage_dir / "memory"
        self._sessions: dict[str, MemorySessionMeta] = {}

    @property
    def storage_dir(self) -> Path:
        return self._storage_dir

    @property
    def memory_dir(self) -> Path:
        return self._memory_dir

    def _main_session_dir(self, session_id: str) -> Path:
        """Get directory for a main agent session."""
        return self._memory_dir / "main" / session_id

    def _subagent_session_dir(self, agent_type: str, session_id: str) -> Path:
        """Get directory for a subagent session."""
        normalized_type = _normalize_agent_type(agent_type)
        return self._memory_dir / "subagents" / normalized_type / session_id

    def _session_dir(self, meta: MemorySessionMeta) -> Path:
        """Get directory for a session based on its metadata."""
        if meta.agent_kind == AgentKind.MAIN:
            return self._main_session_dir(meta.session_id)
        else:
            return self._subagent_session_dir(meta.agent_type, meta.session_id)

    def _state_file(self, meta: MemorySessionMeta) -> Path:
        """Get path to state.json for a session."""
        return self._session_dir(meta) / "state.json"

    def _transcript_file(self, meta: MemorySessionMeta) -> Path:
        """Get path to transcript.jsonl for a session."""
        return self._session_dir(meta) / "transcript.jsonl"

    def create_session(self, meta: MemorySessionMeta) -> MemorySessionMeta:
        """Create a new memory session.

        Raises:
            SessionAlreadyExistsError: If session already exists (in memory or on disk).
            InvalidSessionError: If session metadata is invalid.
        """
        # Validate session_id is filesystem-safe
        if not meta.session_id:
            raise InvalidSessionError("session_id is required")

        if not validate_session_id(meta.session_id):
            raise InvalidSessionError(
                f"session_id contains invalid characters: {meta.session_id}"
            )

        # Validate agent_type
        if not meta.agent_type:
            raise InvalidSessionError("agent_type is required")

        # Check memory cache
        if meta.session_id in self._sessions:
            raise SessionAlreadyExistsError(f"Session {meta.session_id} already exists")

        # Compute session directory and verify path safety
        session_dir = self._session_dir(meta)

        # P0: Verify resolved path is under memory_dir
        if not _validate_path_is_under(session_dir, self._memory_dir):
            raise InvalidSessionError(
                f"Session path escapes memory directory: {session_dir}"
            )

        # P1: Check if session already exists on disk
        state_file = session_dir / "state.json"
        transcript_file = session_dir / "transcript.jsonl"
        if session_dir.exists() and (state_file.exists() or transcript_file.exists()):
            raise SessionAlreadyExistsError(
                f"Session {meta.session_id} already exists on disk at {session_dir}"
            )

        # Create session directory (without exist_ok to avoid race conditions)
        session_dir.mkdir(parents=True, exist_ok=False)

        # Write state.json
        state_file.write_text(json.dumps(meta.to_dict(), indent=2), encoding="utf-8")

        # Create empty transcript.jsonl
        transcript_file.touch()

        # Cache session
        self._sessions[meta.session_id] = meta

        return meta

    def get_session(self, session_id: str) -> MemorySessionMeta | None:
        """Get session metadata by id.

        Returns None if session not found.
        """
        # Check cache first
        if session_id in self._sessions:
            return self._sessions[session_id]

        # Try to load from disk
        # Search in main sessions
        state_file = self._memory_dir / "main" / session_id / "state.json"
        if state_file.exists():
            return self._load_session_from_state(session_id, state_file)

        # Search in subagent sessions
        subagents_dir = self._memory_dir / "subagents"
        if subagents_dir.exists():
            for agent_type_dir in subagents_dir.iterdir():
                if agent_type_dir.is_dir():
                    state_file = agent_type_dir / session_id / "state.json"
                    if state_file.exists():
                        return self._load_session_from_state(session_id, state_file)

        return None

    def _load_session_from_state(
        self, session_id: str, state_file: Path
    ) -> MemorySessionMeta | None:
        """Load session metadata from a state.json file."""
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            meta = MemorySessionMeta.from_dict(data)
            self._sessions[session_id] = meta
            return meta
        except (json.JSONDecodeError, KeyError, ValueError):
            return None

    def list_sessions(
        self,
        agent_kind: AgentKind | None = None,
        agent_type: str | None = None,
    ) -> list[MemorySessionMeta]:
        """List all sessions, optionally filtered by agent_kind and/or agent_type."""
        sessions = []

        # Scan main sessions
        if agent_kind is None or agent_kind == AgentKind.MAIN:
            main_dir = self._memory_dir / "main"
            if main_dir.exists():
                for session_dir in main_dir.iterdir():
                    if session_dir.is_dir():
                        state_file = session_dir / "state.json"
                        if state_file.exists():
                            meta = self._load_session_from_state(
                                session_dir.name, state_file
                            )
                            if meta:
                                if agent_type is None or meta.agent_type == agent_type:
                                    sessions.append(meta)

        # Scan subagent sessions
        if agent_kind is None or agent_kind == AgentKind.SUBAGENT:
            subagents_dir = self._memory_dir / "subagents"
            if subagents_dir.exists():
                for agent_type_dir in subagents_dir.iterdir():
                    if agent_type_dir.is_dir():
                        # Filter by agent_type if specified
                        if (
                            agent_type is not None
                            and agent_type_dir.name != agent_type
                        ):
                            continue
                        for session_dir in agent_type_dir.iterdir():
                            if session_dir.is_dir():
                                state_file = session_dir / "state.json"
                                if state_file.exists():
                                    meta = self._load_session_from_state(
                                        session_dir.name, state_file
                                    )
                                    if meta:
                                        sessions.append(meta)

        return sessions

    def append_entry(self, entry: TranscriptEntry) -> None:
        """Append a transcript entry to a session.

        Raises:
            AppendToUnknownSessionError: If session does not exist.
        """
        meta = self.get_session(entry.session_id)
        if meta is None:
            raise AppendToUnknownSessionError(
                f"Cannot append to unknown session: {entry.session_id}"
            )

        # Append to transcript.jsonl
        transcript_file = self._transcript_file(meta)
        with open(transcript_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")

        # Update counters in metadata
        self._update_counters(meta, entry.type)

    def _update_counters(self, meta: MemorySessionMeta, entry_type: EntryType) -> None:
        """Update session counters based on entry type."""
        if entry_type == EntryType.MESSAGE:
            meta.message_count += 1
        elif entry_type == EntryType.AGENT_EVENT:
            meta.event_count += 1
        elif entry_type == EntryType.SUMMARY:
            meta.summary_count += 1
        elif entry_type == EntryType.EVIDENCE_PACKAGE:
            meta.evidence_count += 1

        meta.updated_at = datetime.now(timezone.utc)

        # Update state.json
        state_file = self._state_file(meta)
        state_file.write_text(json.dumps(meta.to_dict(), indent=2), encoding="utf-8")

    def load_entries(self, session_id: str) -> list[TranscriptEntry]:
        """Load all transcript entries for a session.

        Raises:
            SessionNotFoundError: If session not found.
        """
        meta = self.get_session(session_id)
        if meta is None:
            raise SessionNotFoundError(f"Session not found: {session_id}")

        transcript_file = self._transcript_file(meta)
        if not transcript_file.exists():
            return []

        entries = []
        with open(transcript_file, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    entry = TranscriptEntry.from_dict(data)
                    entries.append(entry)
                except (json.JSONDecodeError, KeyError, ValueError) as e:
                    # Log warning but continue loading
                    print(f"Warning: corrupt entry at line {line_num}: {e}")
                    continue

        return entries
