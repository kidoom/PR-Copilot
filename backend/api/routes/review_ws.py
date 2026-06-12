from __future__ import annotations

import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from backend.agent.runtime.events import RunStatus
from backend.api.routes.review_runs import _run_manager
from backend.agent.runtime.run_manager import RunNotFoundError
from backend.deps import get_agent_deps

logger = logging.getLogger(__name__)

router = APIRouter(tags=["review-ws"])


@router.websocket("/ws/review-runs/{run_id}")
async def review_run_ws(
    websocket: WebSocket,
    run_id: str,
    after_sequence: int = Query(default=-1, alias="after_sequence"),
) -> None:
    """Stream run events over WebSocket.

    For active runs: replay retained events (or persisted events for
    reconnects with ``after_sequence``), then stream live events.

    For recovered terminal/interrupted runs: replay persisted events and
    close.
    """
    has_live = _run_manager.has_run(run_id)
    store = get_agent_deps().pr_session_store

    # Try to resolve from durable store for recovered runs
    ps_id = store.resolve_run_to_pr_session(run_id)

    if not has_live and ps_id is None:
        await websocket.close(code=4004, reason="Run not found")
        return

    await websocket.accept()

    try:
        # If the run is live, use the existing live-streaming path
        if has_live:
            session = _run_manager.get_run(run_id)
            queue = _run_manager.subscribe(run_id)

            # Replay persisted events if reconnecting with after_sequence
            if after_sequence >= 0:
                persisted = store.load_events(run_id, after_sequence=after_sequence, limit=200)
                for pe in persisted:
                    await websocket.send_json({
                        "event_id": pe.event_id,
                        "run_id": pe.run_id,
                        "type": pe.event_type,
                        "sequence": pe.sequence,
                        "created_at": pe.created_at,
                        "payload": pe.payload,
                    })
            else:
                # Replay in-memory retained events
                for event in session.retained_events:
                    await websocket.send_json(event.to_dict())

            if session.status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED):
                await websocket.close(code=1000, reason="Run already finished")
                return

            while True:
                event = await queue.get()
                if event is None:
                    break
                await websocket.send_json(event.to_dict())
                if event.type in ("run.completed", "run.failed", "run.cancelled"):
                    break

            await websocket.close(code=1000)
        else:
            # Recovered run: replay all persisted events
            persisted = store.load_events(run_id, after_sequence=after_sequence, limit=500)
            for pe in persisted:
                await websocket.send_json({
                    "event_id": pe.event_id,
                    "run_id": pe.run_id,
                    "type": pe.event_type,
                    "sequence": pe.sequence,
                    "created_at": pe.created_at,
                    "payload": pe.payload,
                })
            await websocket.close(code=1000, reason="Replayed persisted events")
    except WebSocketDisconnect:
        logger.debug("WS client disconnected for run %s", run_id)
    except Exception:
        logger.warning("WS handler error for run %s", run_id, exc_info=True)
        try:
            await websocket.close(code=1011, reason="Internal server error")
        except Exception:
            pass
