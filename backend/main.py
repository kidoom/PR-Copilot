from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes.pr_context import router as pr_context_router
from backend.api.routes.review import router as review_pipeline_router
from backend.api.routes.review_runs import router as review_runs_router
from backend.api.routes.review_ws import router as review_ws_router
from backend.deps import preload_agent_deps

app = FastAPI(title="PR Copilot", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

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
