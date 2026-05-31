from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.agent.runtime.events import RunStatus
from backend.api.routes.github_auth import get_authenticated_github_session
from backend.api.routes.review_runs import _run_manager
from backend.agent.runtime.run_manager import RunNotFoundError

router = APIRouter(tags=["review-ws"])


@router.websocket("/ws/review-runs/{run_id}")
async def review_run_ws(websocket: WebSocket, run_id: str) -> None:
    if await get_authenticated_github_session(websocket) is None:
        await websocket.close(code=4401, reason="GitHub login required")
        return

    if not _run_manager.has_run(run_id):
        await websocket.close(code=4004, reason="Run not found")
        return

    await websocket.accept()

    session = _run_manager.get_run(run_id)
    queue = _run_manager.subscribe(run_id)

    for event in session.retained_events:
        await websocket.send_json(event.to_dict())

    if session.status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED):
        await websocket.close(code=1000, reason="Run already finished")
        return

    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            await websocket.send_json(event.to_dict())
            if event.type in ("run.completed", "run.failed", "run.cancelled"):
                break

        await websocket.close(code=1000)
    except WebSocketDisconnect:
        pass
