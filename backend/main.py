import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

load_dotenv(".env.local", override=False)

from backend.api.routes.github_auth import get_authenticated_github_session
from backend.api.routes.github_auth import router as github_auth_router
from backend.api.routes.pr_context import router as pr_context_router
from backend.api.routes.review import router as review_pipeline_router
from backend.api.routes.review_runs import router as review_runs_router
from backend.api.routes.review_ws import router as review_ws_router
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


@app.middleware("http")
async def require_github_login(request: Request, call_next):
    if request.url.path.startswith(("/api/pr", "/api/review")):
        session = await get_authenticated_github_session(request)
        if session is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "GitHub login required"},
            )
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(github_auth_router)
app.include_router(pr_context_router)
app.include_router(review_pipeline_router)
app.include_router(review_runs_router)
app.include_router(review_ws_router)


@app.on_event("startup")
async def startup() -> None:
    preload_agent_deps()


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "pr-copilot"}
