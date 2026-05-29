from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.pr_context.routes import router as pr_context_router
from backend.review_pipeline.routes import router as review_pipeline_router

app = FastAPI(title="PR Copilot", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(pr_context_router)
app.include_router(review_pipeline_router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "pr-copilot"}
