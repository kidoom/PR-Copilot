# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PR Copilot is an AI-powered pull request review assistant. It takes a GitHub PR URL, fetches PR metadata and diffs via the GitHub API, builds analysis context (file classification, priority scoring, hunk parsing), runs an AI agent pipeline with specialized sub-agents for code review, and streams findings to a web frontend via WebSocket.

## Commands

```bash
# Run both backend + frontend concurrently
npm run dev

# Backend only (FastAPI on port 8000)
npm run dev:backend

# Frontend only (Vite dev server)
npm run dev:frontend

# Build frontend (TypeScript compile + Vite production build)
cd frontend && npm run build

# Lint frontend
cd frontend && npm run lint

# Run all backend tests
python -m pytest backend/tests/ -v

# Run a single test file
python -m pytest backend/tests/test_api/routes/test_review.py -v

# Run a single test function
python -m pytest backend/tests/test_api/routes/test_review.py::test_function_name -v
```

## Architecture

### Backend (Python / FastAPI)

The backend lives in `backend/` and follows a layered architecture:

- **`backend/api/routes/`** — HTTP and WebSocket route handlers. Key files: `pr_context.py` (PR context CRUD), `review.py` (static review pipeline), `review_runs.py` (async AI review runs), `review_ws.py` (WebSocket event streaming), `github_auth.py` (GitHub App OAuth).

- **`backend/domain/`** — Business logic. `github/` handles GitHub API client and auth. `review/` contains the PR context builder, intake analysis, file prioritization, evidence rules, and context task planning.

- **`backend/agent/`** — AI agent runtime. `runtime/` has the main agent loop, final result assembly, and runner compression. `model/` wraps the OpenAI-compatible LLM client. `tools/` registers repo-context tools (file reading, searching, workspace management). `memory.py` provides file-based session memory. `subagents.py` orchestrates specialized review sub-agents.

- **`backend/main.py`** — FastAPI app entry point, mounts routes and CORS.

### Frontend (React / TypeScript / Vite)

The frontend lives in `frontend/src/`:

- **`api.ts`** — API client for all HTTP calls to the backend.
- **`types.ts`** — TypeScript type definitions matching backend contracts.
- **`components/`** — React components. `ReviewPanel.tsx` is the main review workbench. `FindingCard.tsx` renders individual review findings. `TerminalStream.tsx` shows real-time agent output.

### Communication

- REST API at `/api/*` — proxied from Vite dev server to backend at `localhost:8000`.
- WebSocket at `/ws/*` — real-time streaming of agent events (message deltas, tool calls, sub-agent progress, terminal results). Events include a `sequence` field for client-side ordering and deduplication.

### Agent Pipeline Flow

1. Frontend creates a PR Context (`POST /api/pr/context`) — fetches PR metadata and file list from GitHub.
2. Static review pipeline runs in parallel: intake analysis, file prioritization, evidence rules, context task planning.
3. User starts an AI Review Run (`POST /api/review/runs`) — returns immediately with a `run_id`.
4. Frontend connects WebSocket (`WS /ws/review-runs/{run_id}`) for real-time streaming.
5. Backend runs the main agent loop with sub-agents (security, test context, config, etc.) that explore the repo and produce structured findings.
6. Terminal event carries the final `findings[]` array. Frontend also polls `GET /api/review/runs/{run_id}` after WebSocket closes for the canonical result.

### Key Design Decisions

- **Stateless context**: `context_id` and `run_id` are stored in memory. Backend restart invalidates all existing IDs.
- **Agent compression**: The agent runtime uses context compression strategies (compact, micro-compact, recent, repair) to stay within token limits during long review runs.
- **Evidence vs Findings**: Static rules produce `evidence` (deterministic signals). AI agents produce `findings` (claims with evidence references). Only findings with validated evidence reach the final result.
- **GitHub App auth**: Uses PKCE-based OAuth flow. Browser gets HttpOnly session cookies; backend holds the user access token. `/api/auth/*` and `/api/health` are unauthenticated; all other `/api/*` and WebSocket endpoints require a valid session.

## Environment Variables

Environment is loaded from `.env.local` (gitignored). Key variables:

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | LLM API key |
| `OPENAI_BASE_URL` | OpenAI-compatible API endpoint |
| `OPENAI_MODEL` | Model name (default: `gpt-4o`) |
| `GITHUB_APP_CLIENT_ID` | GitHub App Client ID |
| `GITHUB_APP_CLIENT_SECRET` | GitHub App Client Secret |
| `PR_COPILOT_GITHUB_TOKEN` | Server-side GitHub token for repo access and Checks |
| `PR_COPILOT_STORAGE_DIR` | Agent memory and temp workspace root (default: `~/.pr-copilot`) |
| `PR_COPILOT_LOCAL_REPO_ROOT` | Local repo path for development debugging |

## Git Workflow

- **main branch**: Never push directly. All changes go through PRs with team review.
- **Liziark branch**: Direct push allowed. Development happens here first, then PR to main.
- PR descriptions must include: what changed, what was implemented, what was fixed, test coverage, and verification method.
