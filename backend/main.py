import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv(".env.local", override=False)

from backend.api.routes.pr_context import router as pr_context_router
from backend.api.routes.review import router as review_pipeline_router
from backend.api.routes.review_runs import router as review_runs_router
from backend.api.routes.review_ws import router as review_ws_router
from backend.api.routes.inspection import router as inspection_router
from backend.deps import preload_agent_deps

app = FastAPI(title="PR Copilot", version="0.1.0")

DEFAULT_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]


def _cors_origins() -> list[str]:
    configured = os.environ.get("PR_COPILOT_CORS_ORIGINS", "")
    return DEFAULT_CORS_ORIGINS + [
        origin.strip()
        for origin in configured.split(",")
        if origin.strip()
    ]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(pr_context_router)
app.include_router(review_pipeline_router)
app.include_router(review_runs_router)
app.include_router(review_ws_router)
app.include_router(inspection_router)


@app.on_event("startup")
async def startup() -> None:
    import logging

    from backend.api.routes.review_runs import get_run_manager
    from backend.storage.pr_session.recovery import recover_on_startup

    deps = preload_agent_deps()

    # Recover persisted PR sessions and runs
    logger = logging.getLogger(__name__)
    try:
        report = recover_on_startup(deps.pr_session_store, get_run_manager())
        logger.info(
            "Startup recovery: %d PR sessions, %d runs scanned, "
            "%d terminal restored, %d interrupted",
            report.pr_sessions_scanned,
            report.runs_scanned,
            report.terminal_runs_restored,
            report.interrupted_runs,
        )
    except Exception:
        logger.warning("Startup recovery failed", exc_info=True)


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "pr-copilot"}
