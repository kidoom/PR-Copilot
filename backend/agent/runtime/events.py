from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


RUN_STARTED = "run.started"
MESSAGE_DELTA = "message.delta"
TOOL_CALL = "tool.call"
TOOL_RESULT = "tool.result"
SUBAGENT_STARTED = "subagent.started"
SUBAGENT_COMPLETED = "subagent.completed"
RUN_COMPLETED = "run.completed"
RUN_FAILED = "run.failed"
RUN_CANCELLED = "run.cancelled"

SUPPORTED_EVENT_TYPES = frozenset({
    RUN_STARTED,
    MESSAGE_DELTA,
    TOOL_CALL,
    TOOL_RESULT,
    SUBAGENT_STARTED,
    SUBAGENT_COMPLETED,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_CANCELLED,
})


@dataclass
class RunEvent:
    run_id: str
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    sequence: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "type": self.type,
            "sequence": self.sequence,
            "created_at": self.created_at,
            "payload": self.payload,
        }


@dataclass
class AgentRunSession:
    run_id: str
    context_id: str
    status: RunStatus = RunStatus.QUEUED
    retained_events: list[RunEvent] = field(default_factory=list)
    subscriber_queues: list[Any] = field(default_factory=list)
    cancelling: bool = False
    final_result: dict[str, Any] | None = None
    error_summary: str | None = None
    _next_sequence: int = field(default=0, repr=False)

    def allocate_sequence(self) -> int:
        seq = self._next_sequence
        self._next_sequence += 1
        return seq
