import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.domain.github.local_credentials import resolve_github_token
from backend.domain.github.url_parser import parse_pr_url
from backend.domain.github.client import GitHubClient, GitHubAPIError
from backend.domain.pr_context.context_manager import (
    build_pr_context,
    get_context,
    get_overview_view,
    get_patch_index_view,
    get_file_patch,
)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pr", tags=["pr-context"])


class ContextRequest(BaseModel):
    pr_url: str


@router.post("/context")
async def create_context(req: ContextRequest):
    """Create a PRContext from a GitHub PR URL."""
    try:
        parsed = parse_pr_url(req.pr_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    cred = resolve_github_token()
    client = GitHubClient(token=cred.token or None)
    try:
        try:
            pr_raw = await client.get_pr(parsed.owner, parsed.repo, parsed.pull_number)
            commits_raw = await client.get_commits(parsed.owner, parsed.repo, parsed.pull_number)
            files_raw = await client.get_files(parsed.owner, parsed.repo, parsed.pull_number)
        except GitHubAPIError as e:
            status = e.status_code
            if status == 401:
                raise HTTPException(status_code=401, detail=_auth_guidance(e.message))
            if status == 403:
                if e.error_category == "rate_limit":
                    raise HTTPException(status_code=429, detail=_auth_guidance(e.message))
                raise HTTPException(status_code=403, detail=_auth_guidance(e.message))
            if status == 404:
                raise HTTPException(status_code=404, detail=e.message)
            if status == 504:
                raise HTTPException(
                    status_code=504,
                    detail="连接 GitHub API 超时，请检查网络或代理设置后重试。",
                )
            if status == 502:
                raise HTTPException(
                    status_code=502,
                    detail=f"无法连接 GitHub API，请检查网络、代理和 TLS 设置。详情：{e.message}",
                )
            raise HTTPException(status_code=502, detail=e.message)
    finally:
        await client.close()

    ctx = await build_pr_context(
        pr_raw, commits_raw, files_raw,
        owner=parsed.owner, repo=parsed.repo, pull_number=parsed.pull_number,
    )

    # Ensure PR session exists on disk (context is saved when a run is created)
    try:
        from backend.deps import get_agent_deps
        store = get_agent_deps().pr_session_store
        store.get_or_create_pr_session(
            parsed.owner, parsed.repo, parsed.pull_number
        )
    except Exception:
        logger.warning("Failed to ensure PR session exists", exc_info=True)

    return get_overview_view(ctx)


@router.get("/sessions")
async def list_all_sessions():
    """List all PR sessions with their latest run info."""
    from backend.deps import get_agent_deps
    store = get_agent_deps().pr_session_store

    sessions = store.list_all_sessions(limit=30)
    result = []
    for ps in sessions:
        session_info: dict = {
            "pr_session_id": ps.pr_session_id,
            "owner": ps.owner,
            "repo": ps.repo,
            "pull_number": ps.pull_number,
            "updated_at": ps.updated_at,
            "run_count": ps.run_count,
        }
        # Get latest run
        try:
            runs = store.list_runs(ps.pr_session_id, limit=1)
            if runs:
                latest = runs[0]
                session_info["latest_run_id"] = latest.run_id
                session_info["latest_lifecycle"] = latest.lifecycle
                session_info["latest_completed_at"] = latest.completed_at
                session_info["latest_finding_count"] = latest.finding_count
                if latest.lifecycle in ("completed", "failed", "cancelled"):
                    try:
                        res = store.load_result(latest.run_id)
                        if res:
                            session_info["latest_summary"] = res.summary
                            session_info["latest_status"] = res.status
                    except Exception:
                        pass
        except Exception:
            pass
        result.append(session_info)

    return {"sessions": result}


def _auth_guidance(original_message: str) -> dict:
    """Return actionable guidance for private repo or rate-limit errors."""
    return {
        "message": original_message,
        "guidance": (
            "GitHub credentials are needed. Run `gh auth login` or set "
            "the GH_TOKEN environment variable, then retry."
        ),
    }


@router.get("/context/{context_id}/patch-index")
async def patch_index(context_id: str):
    """Return Patch Index View for an existing PRContext."""
    ctx = get_context(context_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="Context not found")
    return get_patch_index_view(ctx)


@router.get("/context/{context_id}/files/{filename:path}/patch")
async def file_patch(
    context_id: str,
    filename: str,
    hunk_index: int | None = None,
    max_lines: int = 500,
):
    """Return patch data for a specific file."""
    ctx = get_context(context_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="Context not found")
    try:
        return get_file_patch(ctx, filename, hunk_index, max_lines=max_lines)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except IndexError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/context/{context_id}/runs")
async def list_review_runs(context_id: str):
    """List all review runs for a PR context (from durable store)."""
    ctx = get_context(context_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="Context not found")

    from backend.deps import get_agent_deps
    store = get_agent_deps().pr_session_store

    ps = store.get_pr_session_by_identity(ctx.owner, ctx.repo, ctx.pull_number)
    if ps is None:
        return {"runs": []}

    try:
        entries = store.list_runs(ps.pr_session_id, limit=50)
    except Exception:
        logger.warning("Failed to list runs for %s", ps.pr_session_id, exc_info=True)
        return {"runs": []}

    runs = []
    for e in entries:
        run_info: dict = {
            "run_id": e.run_id,
            "lifecycle": e.lifecycle,
            "created_at": e.created_at,
            "completed_at": e.completed_at,
            "finding_count": e.finding_count,
        }
        # Load result summary if available
        if e.lifecycle in ("completed", "failed", "cancelled"):
            try:
                result = store.load_result(e.run_id)
                if result:
                    run_info["summary"] = result.summary
                    run_info["status"] = result.status
            except Exception:
                pass
        runs.append(run_info)

    return {"runs": runs}
