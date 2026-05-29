from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..github.url_parser import parse_pr_url
from ..github.client import GitHubClient, GitHubAPIError
from .context_manager import (
    build_pr_context,
    get_context,
    get_overview_view,
    get_patch_index_view,
    get_file_patch,
)

router = APIRouter(prefix="/api/pr", tags=["pr-context"])


class ContextRequest(BaseModel):
    pr_url: str
    github_token: str | None = None


@router.post("/context")
async def create_context(req: ContextRequest):
    """Create a PRContext from a GitHub PR URL."""
    try:
        parsed = parse_pr_url(req.pr_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    client = GitHubClient(token=req.github_token)
    try:
        try:
            pr_raw = await client.get_pr(parsed.owner, parsed.repo, parsed.pull_number)
            commits_raw = await client.get_commits(parsed.owner, parsed.repo, parsed.pull_number)
            files_raw = await client.get_files(parsed.owner, parsed.repo, parsed.pull_number)
        except GitHubAPIError as e:
            status = e.status_code
            if status == 401:
                raise HTTPException(status_code=401, detail=e.message)
            if status == 403:
                raise HTTPException(status_code=429, detail=e.message)
            if status == 404:
                raise HTTPException(status_code=404, detail=e.message)
            raise HTTPException(status_code=502, detail=e.message)
    finally:
        await client.close()

    ctx = await build_pr_context(pr_raw, commits_raw, files_raw)
    return get_overview_view(ctx)


@router.get("/context/{context_id}/patch-index")
async def patch_index(context_id: str):
    """Return Patch Index View for an existing PRContext."""
    ctx = get_context(context_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="Context not found")
    return get_patch_index_view(ctx)


@router.get("/context/{context_id}/files/{filename:path}/patch")
async def file_patch(context_id: str, filename: str, hunk_index: int | None = None):
    """Return patch data for a specific file."""
    ctx = get_context(context_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="Context not found")
    try:
        return get_file_patch(ctx, filename, hunk_index)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except IndexError as e:
        raise HTTPException(status_code=400, detail=str(e))
