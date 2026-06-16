# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with this repository.

## Project Overview

PR Copilot is an AI-powered pull request review assistant. It takes a GitHub PR URL, fetches PR metadata and diffs via GitHub API, builds deterministic review context, packages PR evidence into token-aware reviewer scopes, runs an Open Multi-Agent (OMA) review team, and streams findings to a React frontend over WebSocket.

## Commands

```bash
# Run TypeScript backend + frontend concurrently
npm run dev

# TypeScript backend only (Express + OMA on port 8000)
npm run dev:server
npm run dev:backend

# Frontend only (Vite dev server)
npm run dev:frontend

# Server checks
cd server && npm test
cd server && npm run lint
cd server && npm run build

# Frontend checks
cd frontend && npm run build
cd frontend && npm run lint
```

## Architecture

### Backend (TypeScript / Express / OMA)

The active backend lives in `server/`:

- `server/src/index.ts`: Express app entry point, CORS, REST routes, WebSocket server.
- `server/src/config.ts`: environment loading.
- `server/src/github/`: Octokit integration for PR metadata, file diffs, commits, and Checks API.
- `server/src/review/`: static review pipeline for file classification, priority scoring, evidence rules, context task planning, and token-aware review packaging.
- `server/src/agent/`: OMA review team, coordinator prompt, specialized agents, repo tools, context compression, and `runReview()`.
- `server/src/agent/tools/`: OMA `defineTool()` repo-context tools with path safety, sensitive-file filtering, per-reviewer scope enforcement, and budgets.
- `server/src/agent/compression/`: CJK-aware token estimation, micro-compact, repair, auto-compact, recent selection, and custom OMA `ContextStrategy`.
- `server/src/ws/`: WebSocket subscription, event replay, and event-log persistence.
- `server/src/store/`: JSON session store for PR contexts, review runs, and event logs.

### Removed Python Backend

The previous Python FastAPI backend has been removed. Do not add new backend features under `backend/`; the active backend is `server/`. The root `npm run dev` path starts the TypeScript backend and frontend.

### Frontend (React / TypeScript / Vite)

The frontend lives in `frontend/src/`:

- `api.ts`: compatibility client for TS backend responses and legacy frontend shapes.
- `types.ts`: TypeScript API and UI contracts.
- `components/ReviewPanel.tsx`: review run control, polling fallback, and event handling.
- `components/FindingCard.tsx`: rendered review findings.
- `components/TerminalStream.tsx`: live agent event stream.

## API And Streaming

- REST API: `/api/*`, proxied by Vite to `localhost:8000`.
- WebSocket: `/ws/review-runs/:run_id`, supports `?after_sequence=N` replay.
- Event format: `{ event_id, run_id, type, sequence, created_at, payload }`.
- Terminal events: `run.completed`, `run.failed`, `run.cancelled`.

## Agent Pipeline

1. Frontend creates a PR context with `POST /api/pr/context`.
2. Server fetches PR metadata/diffs and persists the context.
3. Static review produces classifications, evidence signals, and context tasks.
4. `buildReviewPackage()` creates compact diff slices and per-reviewer scopes.
5. Frontend starts a review run with `POST /api/review/runs`.
6. `runReview()` creates the OMA team and runs the Coordinator DAG.
7. Specialized agents use scoped read-only repo tools, per-agent budgets, and per-agent compression.
8. WebSocket events stream progress and terminal findings.
9. Review runs and events are persisted for polling and reconnect replay.

## Environment Variables

| Variable | Purpose |
|---|---|
| `PORT` | TS backend port, default `8000` |
| `OPENAI_API_KEY` | LLM API key |
| `OPENAI_BASE_URL` | OpenAI-compatible API endpoint |
| `OPENAI_MODEL` | Model name, default `gpt-4o` |
| `GITHUB_TOKEN` | GitHub token fallback |
| `GITHUB_APP_ID` | GitHub App ID |
| `GITHUB_APP_PRIVATE_KEY` | GitHub App private key |
| `PR_COPILOT_STORAGE_DIR` | JSON session/event storage root, default `~/.pr-copilot` |
| `PR_COPILOT_REVIEW_MAX_CONCURRENCY` | OMA review task concurrency, default `1`; increase only if the model provider tolerates parallel large-context calls |

## Git Workflow

- `main`: never push directly. Use PRs and team review.
- `Liziark`: direct push is allowed. Develop here first, then open a PR to `main`.
- PR descriptions must include: what changed, what was implemented, what was fixed, test coverage, and verification method.
