from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.agent.runtime.main_runner import run_main_agent
from backend.agent.tools.repo_context.provider.models import PRIdentity
from backend.agent.runtime.run_manager import RunManager, RunNotFoundError
from backend.deps import get_agent_deps
from backend.domain.github.local_credentials import resolve_github_token
from backend.domain.pr_context.context_manager import get_context
from backend.domain.review.context_task_planner import build_context_task_plan
from backend.domain.review.evidence import analyze as analyze_evidence
from backend.storage.pr_session.context_serializer import serialize_context
from backend.storage.pr_session.models import TaskPlanRecord
from backend.storage.pr_session.models import RunLifecycle
from backend.storage.pr_session.run_persistence import (
    create_durable_run,
    persist_context,
    persist_task_plan,
)
from backend.storage.pr_session.store import UnsupportedSchemaError

logger = logging.getLogger(__name__)

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

    # Check for existing active run for this context
    try:
        from backend.agent.runtime.events import RunStatus
        active_statuses = {RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.CANCELLING}
        for existing_run in list(_run_manager._runs.values()):
            if existing_run.context_id == req.context_id and existing_run.status in active_statuses:
                raise HTTPException(
                    status_code=409,
                    detail=f"已有一个正在进行的审查任务 (run_id: {existing_run.run_id})，请等待完成或取消后再试。"
                )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Failed to check active runs: %s", exc)

    evidence = analyze_evidence(ctx)
    task_plan = build_context_task_plan(ctx, evidence)

    # Create durable run first to get the canonical run_id
    store = get_agent_deps().pr_session_store
    durable_run_id: str | None = None
    try:
        pr_meta, run_state = create_durable_run(
            store,
            owner=ctx.owner,
            repo=ctx.repo,
            pull_number=ctx.pull_number,
            context_id=req.context_id,
            base_sha=ctx.commits.commits[0].sha if ctx.commits.commits else "",
            head_sha=ctx.commits.head_sha,
        )
        durable_run_id = run_state.run_id

        # Persist context
        ctx_record = serialize_context(ctx, pr_meta.pr_session_id)
        persist_context(store, durable_run_id, ctx_record)

        # Persist task plan
        tp_record = TaskPlanRecord(
            run_id=durable_run_id,
            pr_session_id=pr_meta.pr_session_id,
            tasks=task_plan.get("tasks", []),
            coverage_manifest=task_plan.get("coverage_manifest", {}),
            evidence=[e for e in (evidence if isinstance(evidence, list) else [])],
        )
        persist_task_plan(store, durable_run_id, tp_record)
    except Exception:
        logger.warning("Failed to create durable run record", exc_info=True)

    # Use the durable run_id for the in-memory run so both share the same ID
    run = _run_manager.create_run(req.context_id, run_id=durable_run_id)

    cred = resolve_github_token()
    task = asyncio.create_task(
        _execute_run(
            run.run_id,
            req.context_id,
            task_plan,
            req.local_repo_root,
            cred.token or None,
        )
    )
    _run_manager.register_execution_task(run.run_id, task)

    return {"run_id": run.run_id, "status": run.status.value}


async def _execute_run(
    run_id: str,
    context_id: str,
    task_plan: dict[str, Any],
    local_repo_root: str | None = None,
    token: str | None = None,
) -> None:
    try:
        ctx = get_context(context_id)
        if ctx is None:
            _run_manager.fail_run(run_id, error=f"Context not found: {context_id}")
            return

        deps = get_agent_deps()
        repo_root = local_repo_root or os.environ.get("PR_COPILOT_LOCAL_REPO_ROOT", "")
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
            pr_context=ctx,
            repo_root=repo_root,
            deps=deps,
            run_manager=_run_manager,
            workspace_manager=deps.workspace_manager,
            pr_identity=pr_identity,
            token=token,
        )
    except Exception as exc:
        logger.error("Run %s failed before agent started: %s", run_id, exc, exc_info=True)
        _run_manager.fail_run(run_id, error=f"{type(exc).__name__}: {exc}")


@router.get("/{run_id}")
async def get_run_status(run_id: str):
    try:
        return _run_manager.get_status(run_id)
    except RunNotFoundError:
        # Try to recover from durable store
        store = get_agent_deps().pr_session_store
        ps_id = store.resolve_run_to_pr_session(run_id)
        if ps_id is not None:
            try:
                state = store.get_run_state(run_id)
                result = store.load_result(run_id)
                d: dict[str, Any] = {
                    "run_id": state.run_id,
                    "context_id": state.context_id,
                    "status": state.lifecycle.value,
                }
                if state.error_summary:
                    d["error_summary"] = state.error_summary
                if result:
                    d["final_result"] = {
                        "findings": result.findings,
                        "coverage": result.coverage,
                        "usage": result.usage,
                    }
                if state.lifecycle == RunLifecycle.INTERRUPTED:
                    d["interrupted"] = True

                # Indicate when the persisted context cannot be hydrated
                ctx_available = get_context(state.context_id) is not None
                if not ctx_available:
                    try:
                        store.load_context(run_id)
                        d["context_status"] = "available_not_loaded"
                    except UnsupportedSchemaError as exc:
                        d["context_status"] = "unsupported_version"
                        d["context_unavailable_reason"] = (
                            f"Schema v{exc.stored_version} > max supported v{exc.max_supported_version}"
                        )
                    except Exception:
                        d["context_status"] = "unavailable"
                        d["context_unavailable_reason"] = "Context file missing or corrupt"

                return d
            except Exception:
                pass
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: str):
    try:
        _run_manager.cancel_run(run_id)
        return {"run_id": run_id, "status": _run_manager.get_run(run_id).status.value}
    except RunNotFoundError:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
