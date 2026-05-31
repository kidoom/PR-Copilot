from __future__ import annotations

import asyncio
import uuid
from typing import Any

from backend.agent.runtime.events import (
    AgentRunSession,
    RunEvent,
    RunStatus,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_CANCELLED,
)


class RunNotFoundError(Exception):
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"Run not found: {run_id}")


class RunManager:
    def __init__(self, *, max_retained_events: int = 100) -> None:
        self._runs: dict[str, AgentRunSession] = {}
        self._max_retained_events = max_retained_events
        self._execution_tasks: dict[str, asyncio.Task] = {}
        self._cancellation_probes: dict[str, Any] = {}  # CancellationProbe by run_id

    def create_run(self, context_id: str, *, run_id: str | None = None) -> AgentRunSession:
        rid = run_id or f"run_{uuid.uuid4().hex[:12]}"
        session = AgentRunSession(run_id=rid, context_id=context_id)
        self._runs[rid] = session
        return session

    def get_run(self, run_id: str) -> AgentRunSession:
        session = self._runs.get(run_id)
        if session is None:
            raise RunNotFoundError(run_id)
        return session

    def has_run(self, run_id: str) -> bool:
        return run_id in self._runs

    def publish_event(self, run_id: str, event_type: str, payload: dict[str, Any] | None = None) -> RunEvent:
        session = self.get_run(run_id)
        seq = session.allocate_sequence()
        event = RunEvent(
            run_id=run_id,
            type=event_type,
            payload=payload or {},
            sequence=seq,
        )
        session.retained_events.append(event)
        if len(session.retained_events) > self._max_retained_events:
            session.retained_events = session.retained_events[-self._max_retained_events:]

        for queue in session.subscriber_queues:
            queue.put_nowait(event)

        return event

    def subscribe(self, run_id: str) -> Any:
        import asyncio
        session = self.get_run(run_id)
        queue: asyncio.Queue[RunEvent | None] = asyncio.Queue()
        session.subscriber_queues.append(queue)
        return queue

    def get_retained_events(self, run_id: str) -> list[RunEvent]:
        session = self.get_run(run_id)
        return list(session.retained_events)

    def get_status(self, run_id: str) -> dict[str, Any]:
        session = self.get_run(run_id)
        result: dict[str, Any] = {
            "run_id": session.run_id,
            "context_id": session.context_id,
            "status": session.status.value,
        }
        if session.final_result is not None:
            result["final_result"] = session.final_result
        if session.error_summary is not None:
            result["error_summary"] = session.error_summary
        return result

    def mark_running(self, run_id: str) -> None:
        session = self.get_run(run_id)
        if session.status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED):
            return
        session.status = RunStatus.RUNNING

    def register_execution_task(self, run_id: str, task: asyncio.Task) -> None:
        """Register a background asyncio.Task for a review run (task 3.1)."""
        self._execution_tasks[run_id] = task

    def get_execution_task(self, run_id: str) -> asyncio.Task | None:
        """Look up the registered execution task for a run (task 3.1)."""
        return self._execution_tasks.get(run_id)

    def register_cancellation_probe(self, run_id: str, probe: Any) -> None:
        """Register a CancellationProbe for a run so cancel_run can signal it."""
        self._cancellation_probes[run_id] = probe

    def _is_terminal(self, session: AgentRunSession) -> bool:
        """Check if a session is in a terminal state (task 3.6)."""
        return session.status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED)

    def complete_run(self, run_id: str, result: dict[str, Any] | None = None) -> None:
        session = self.get_run(run_id)
        if self._is_terminal(session):  # task 3.6: prevent overwrite
            return
        session.status = RunStatus.COMPLETED
        session.final_result = result
        self._cleanup_execution_task(run_id)  # task 3.8
        self.publish_event(run_id, RUN_COMPLETED, payload=result or {})

    def fail_run(self, run_id: str, error: str) -> None:
        session = self.get_run(run_id)
        if self._is_terminal(session):  # task 3.6: prevent overwrite
            return
        session.status = RunStatus.FAILED
        session.error_summary = error
        self._cleanup_execution_task(run_id)  # task 3.8
        self.publish_event(run_id, RUN_FAILED, payload={"error": error})

    def cancel_run(self, run_id: str) -> None:
        """Cancel a run with idempotent semantics (tasks 3.3-3.7).

        - Queued run: transition directly to cancelled.
        - Active run: set cancelling flag and cancel execution task.
        - Already terminal: no-op (idempotent).
        """
        session = self.get_run(run_id)

        # Already terminal: no-op (task 3.5 idempotent)
        if self._is_terminal(session):
            return

        # Already cancelling: no-op (task 3.5 idempotent)
        if session.status == RunStatus.CANCELLING:
            return

        # Queued run: transition directly to cancelled (task 3.3)
        if session.status == RunStatus.QUEUED:
            session.status = RunStatus.CANCELLED
            session.cancelling = True
            # Cancel the probe first
            probe = self._cancellation_probes.get(run_id)
            if probe is not None:
                probe.cancel()
            # Cancel the task BEFORE cleaning up reference
            task = self._execution_tasks.get(run_id)
            if task is not None and not task.done():
                task.cancel()
            self._cleanup_execution_task(run_id)  # task 3.8
            self.publish_event(run_id, RUN_CANCELLED)  # task 3.7: exactly one
            return

        # Active run: set cancelling, cancel probe and task (task 3.4)
        session.cancelling = True
        session.status = RunStatus.CANCELLING
        # Cancel the probe so cooperative checkpoints observe cancellation
        probe = self._cancellation_probes.get(run_id)
        if probe is not None:
            probe.cancel()
        task = self._execution_tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()

    def observe_cancellation(self, run_id: str) -> None:
        """Transition from cancelling to cancelled and publish terminal event."""
        session = self.get_run(run_id)
        if session.status != RunStatus.CANCELLING:
            return
        session.status = RunStatus.CANCELLED
        self._cleanup_execution_task(run_id)  # task 3.8
        self.publish_event(run_id, RUN_CANCELLED)  # task 3.7: exactly one

    def is_cancelling(self, run_id: str) -> bool:
        try:
            session = self.get_run(run_id)
            return session.cancelling
        except RunNotFoundError:
            return False

    def _cleanup_execution_task(self, run_id: str) -> None:
        """Remove registered background task reference after terminal state (task 3.8)."""
        self._execution_tasks.pop(run_id, None)
        self._cancellation_probes.pop(run_id, None)
