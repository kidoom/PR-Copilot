from __future__ import annotations

from typing import Any

from backend.agent.runtime.memory.models import EntryType, TranscriptEntry
from backend.agent.runtime.memory.store import (
    AppendToUnknownSessionError,
    FileMemoryStore,
    SessionNotFoundError,
)


def append_message(
    store: FileMemoryStore,
    session_id: str,
    message: dict[str, Any],
) -> TranscriptEntry:
    """Append a message entry to a session.

    Args:
        store: The memory store.
        session_id: Target session id.
        message: Message payload (role, content, etc.)

    Returns:
        The created transcript entry.

    Raises:
        AppendToUnknownSessionError: If session does not exist.
    """
    entry = TranscriptEntry.create(
        session_id=session_id,
        entry_type=EntryType.MESSAGE,
        payload=message,
    )
    store.append_entry(entry)
    return entry


def append_event(
    store: FileMemoryStore,
    session_id: str,
    event: dict[str, Any],
) -> TranscriptEntry:
    """Append an agent event entry to a session.

    Args:
        store: The memory store.
        session_id: Target session id.
        event: Event payload (event_type, data, etc.)

    Returns:
        The created transcript entry.

    Raises:
        AppendToUnknownSessionError: If session does not exist.
    """
    entry = TranscriptEntry.create(
        session_id=session_id,
        entry_type=EntryType.AGENT_EVENT,
        payload=event,
    )
    store.append_entry(entry)
    return entry


def append_summary(
    store: FileMemoryStore,
    session_id: str,
    summary: dict[str, Any],
) -> TranscriptEntry:
    """Append a summary entry to a session.

    Args:
        store: The memory store.
        session_id: Target session id.
        summary: Summary payload (summary_text, recent_messages, etc.)

    Returns:
        The created transcript entry.

    Raises:
        AppendToUnknownSessionError: If session does not exist.
    """
    entry = TranscriptEntry.create(
        session_id=session_id,
        entry_type=EntryType.SUMMARY,
        payload=summary,
    )
    store.append_entry(entry)
    return entry


def append_todo_state(
    store: FileMemoryStore,
    session_id: str,
    todos: list[dict[str, str]],
) -> TranscriptEntry:
    """Append a todo state entry to a session.

    Args:
        store: The memory store.
        session_id: Target session id.
        todos: List of todo items with content and status.

    Returns:
        The created transcript entry.

    Raises:
        AppendToUnknownSessionError: If session does not exist.
    """
    entry = TranscriptEntry.create(
        session_id=session_id,
        entry_type=EntryType.TODO_STATE,
        payload={"todos": todos},
    )
    store.append_entry(entry)
    return entry


def append_evidence_package(
    store: FileMemoryStore,
    session_id: str,
    package: dict[str, Any],
) -> TranscriptEntry:
    """Append an evidence package entry to a session.

    Args:
        store: The memory store.
        session_id: Target session id.
        package: Evidence package payload.

    Returns:
        The created transcript entry.

    Raises:
        AppendToUnknownSessionError: If session does not exist.
    """
    entry = TranscriptEntry.create(
        session_id=session_id,
        entry_type=EntryType.EVIDENCE_PACKAGE,
        payload=package,
    )
    store.append_entry(entry)
    return entry
