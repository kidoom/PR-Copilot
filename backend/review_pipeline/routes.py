from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.pr_context.context_manager import get_context
from backend.review_pipeline.intake import build_intake_summary

router = APIRouter(prefix="/api/review", tags=["review-pipeline"])


class IntakeRequest(BaseModel):
    context_id: str


@router.post("/intake")
async def intake_summary(req: IntakeRequest):
    ctx = get_context(req.context_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Context not found: {req.context_id}")
    return build_intake_summary(ctx)
