from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.agent.runtime.main_runner import run_main_agent
from backend.agent.tools.repo_context.provider.models import PRIdentity
from backend.agent.runtime.run_manager import RunManager, RunNotFoundError
from backend.deps import get_agent_deps
from backend.domain.pr_context.context_manager import get_context
from backend.domain.review.context_task_planner import build_context_task_plan
from backend.domain.review.evidence import analyze as analyze_evidence

router = APIRouter(prefix="/api/review/runs", tags=["review-runs"])

_run_manager = RunManager()


def get_run_manager() -> RunManager:
    return _run_manager


class CreateRunRequest(BaseModel):
    context_id: str
    local_repo_root: str | None = None


@router.post("")
async def create_run(req: CreateRunRequest):
    ctx = get_context(req.context_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Context not found: {req.context_id}")

    evidence = analyze_evidence(ctx)
    task_plan = build_context_task_plan(ctx, evidence)

    run = _run_manager.create_run(req.context_id)

    asyncio.create_task(_execute_run(run.run_id, req.context_id, task_plan, req.local_repo_root))

    return {"run_id": run.run_id, "status": run.status.value}


async def _execute_run(
    run_id: str,
    context_id: str,
    task_plan: dict[str, Any],
    local_repo_root: str | None = None,
) -> None:
    ctx = get_context(context_id)
    pr_context = ctx
    deps = get_agent_deps()

    repo_root = local_repo_root or os.environ.get("PR_COPILOT_LOCAL_REPO_ROOT", "")
    pr_identity = None
    if ctx is not None:
        pr_identity = PRIdentity(
            owner=ctx.owner,
            repo=ctx.repo,
            pull_number=ctx.pull_number,
            head_sha=ctx.commits.head_sha,
            base_ref=ctx.pr.base_branch,
        )

    await run_main_agent(
        run_id=run_id,
        context_id=context_id,
        task_plan=task_plan,
        pr_context=pr_context,
        repo_root=repo_root,
        deps=deps,
        run_manager=_run_manager,
        workspace_manager=deps.workspace_manager,
        pr_identity=pr_identity,
        token=os.environ.get("PR_COPILOT_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN"),
    )


@router.get("/{run_id}")
async def get_run_status(run_id: str):
    try:
        return _run_manager.get_status(run_id)
    except RunNotFoundError:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: str):
    try:
        _run_manager.cancel_run(run_id)
        return {"run_id": run_id, "status": _run_manager.get_run(run_id).status.value}
    except RunNotFoundError:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
