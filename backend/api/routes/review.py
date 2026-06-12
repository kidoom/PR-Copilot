import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.domain.pr_context.context_manager import get_context
from backend.domain.review.intake import build_intake_summary
from backend.domain.review.file_priority import build_file_priority_view
from backend.domain.review.evidence import build_evidence_response, analyze as analyze_evidence
from backend.domain.review.context_task_planner import build_context_task_plan

router = APIRouter(prefix="/api/review", tags=["review-pipeline"])


def _is_diagnostics_enabled() -> bool:
    """Check if diagnostics mode is enabled via environment variable."""
    return os.environ.get("PR_COPILOT_DIAGNOSTICS", "").strip().lower() in ("1", "true", "yes", "on")


class IntakeRequest(BaseModel):
    context_id: str


@router.post("/intake")
async def intake_summary(req: IntakeRequest):
    ctx = get_context(req.context_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Context not found: {req.context_id}")
    return build_intake_summary(ctx)


@router.post("/file-priority")
async def file_priority(req: IntakeRequest):
    if not _is_diagnostics_enabled():
        raise HTTPException(status_code=404, detail="Diagnostics mode not enabled")
    ctx = get_context(req.context_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Context not found: {req.context_id}")
    return build_file_priority_view(ctx.context_id, ctx.files)


@router.post("/evidence")
async def evidence(req: IntakeRequest):
    if not _is_diagnostics_enabled():
        raise HTTPException(status_code=404, detail="Diagnostics mode not enabled")
    ctx = get_context(req.context_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Context not found: {req.context_id}")
    return build_evidence_response(ctx)


@router.post("/context-tasks")
async def context_tasks(req: IntakeRequest):
    if not _is_diagnostics_enabled():
        raise HTTPException(status_code=404, detail="Diagnostics mode not enabled")
    ctx = get_context(req.context_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Context not found: {req.context_id}")
    evidence_items = analyze_evidence(ctx)
    return build_context_task_plan(ctx, evidence_items)
