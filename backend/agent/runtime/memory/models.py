from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class AgentKind(str, Enum):
    MAIN = "main"
    SUBAGENT = "subagent"


class EntryType(str, Enum):
    MESSAGE = "message"
    AGENT_EVENT = "agent_event"
    SESSION_META = "session_meta"
    SUMMARY = "summary"
    TODO_STATE = "todo_state"
    EVIDENCE_PACKAGE = "evidence_package"


# Type alias for transcript entry types
TranscriptEntryType = EntryType


@dataclass
class MemorySessionMeta:
    """Metadata for a memory session."""

    session_id: str
    run_id: str
    agent_kind: AgentKind
    agent_type: str
    context_id: str
    task_id: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Counters
    message_count: int = 0
    event_count: int = 0
    summary_count: int = 0
    evidence_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for state.json."""
        return {
            "session_id": self.session_id,
            "run_id": self.run_id,
            "agent_kind": self.agent_kind.value,
            "agent_type": self.agent_type,
            "context_id": self.context_id,
            "task_id": self.task_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "message_count": self.message_count,
            "event_count": self.event_count,
            "summary_count": self.summary_count,
            "evidence_count": self.evidence_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemorySessionMeta:
        """Deserialize from dictionary."""
        return cls(
            session_id=data["session_id"],
            run_id=data["run_id"],
            agent_kind=AgentKind(data["agent_kind"]),
            agent_type=data["agent_type"],
            context_id=data["context_id"],
            task_id=data.get("task_id", ""),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            message_count=data.get("message_count", 0),
            event_count=data.get("event_count", 0),
            summary_count=data.get("summary_count", 0),
            evidence_count=data.get("evidence_count", 0),
        )


@dataclass
class TranscriptEntry:
    """A single entry in a session transcript."""

    entry_id: str
    session_id: str
    type: EntryType
    created_at: datetime
    schema_version: int = 1
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for JSONL."""
        return {
            "entry_id": self.entry_id,
            "session_id": self.session_id,
            "type": self.type.value,
            "created_at": self.created_at.isoformat(),
            "schema_version": self.schema_version,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TranscriptEntry:
        """Deserialize from dictionary."""
        return cls(
            entry_id=data["entry_id"],
            session_id=data["session_id"],
            type=EntryType(data["type"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            schema_version=data.get("schema_version", 1),
            payload=data.get("payload", {}),
        )

    @classmethod
    def create(
        cls,
        session_id: str,
        entry_type: EntryType,
        payload: dict[str, Any],
    ) -> TranscriptEntry:
        """Create a new transcript entry with generated id and timestamp."""
        return cls(
            entry_id=str(uuid.uuid4()),
            session_id=session_id,
            type=entry_type,
            created_at=datetime.now(timezone.utc),
            payload=payload,
        )
