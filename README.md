# PR Copilot 
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/kidoom/PR-Copilot)

PR Copilot is an AI-powered pull request review assistant. It fetches GitHub PR metadata and diffs, builds deterministic review context, packages PR evidence into token-aware reviewer scopes, runs an Open Multi-Agent (OMA) review team, and streams structured findings to a React frontend over Server-Sent Events.

## Stack

| Layer | Technology |
| --- | --- |
| Backend | TypeScript, Express, Open Multi-Agent, Octokit |
| Frontend | React, TypeScript, Vite, Tailwind CSS |
| Streaming | Server-Sent Events with replay by sequence |
| Storage | JSON session store under `PR_COPILOT_STORAGE_DIR` |
| LLM | OpenAI-compatible API via `OPENAI_*` env vars |

## Project Structure

```text
PR-Copilot/
├── server/                  # TypeScript backend
│   ├── src/github/          # GitHub PR metadata, diffs, checks
│   ├── src/review/          # Static review pipeline
│   ├── src/agent/           # OMA team, tools, compression, run orchestration
│   └── src/store/           # JSON persistence
├── frontend/                # React review workbench
├── docs/                    # Design and project notes
├── openspec/                # OpenSpec changes
└── package.json             # Root dev scripts
```

The previous Python FastAPI backend has been removed. Use `server/` for all backend work.

## Review Architecture

The backend uses a package-first review pipeline so OMA agents can run with bounded context:

1. Static review classifies changed files, scores priority, and extracts evidence signals.
2. A token-aware review package keeps compact diff slices for the highest-priority files and records omitted files explicitly.
3. Each reviewer gets an independent scope with allowed files, search scopes, and tool budgets.
4. Repo tools enforce those scopes in code, so agents cannot broaden into full-repository reads by prompt alone.
5. The coordinator synthesizes scoped reviewer outputs into structured findings.

Keep `PR_COPILOT_REVIEW_MAX_CONCURRENCY=1` for conservative providers. Raise it cautiously after confirming the selected model can handle the scoped parallel request load.

## Quick Start

Create `.env.local` in the repository root:

```env
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o
GITHUB_TOKEN=your-github-token
PORT=8000
PR_COPILOT_STORAGE_DIR=~/.pr-copilot
PR_COPILOT_REVIEW_MAX_CONCURRENCY=1
```

Install dependencies:

```bash
npm install
npm install --prefix server
npm install --prefix frontend
```

Run both backend and frontend:

```bash
npm run dev
```

Run separately:

```bash
npm run dev:server
npm run dev:frontend
```

Frontend: `http://localhost:5173`  
Backend: `http://localhost:8000`

## API Smoke

```bash
curl -X POST http://localhost:8000/api/pr/context \
  -H "Content-Type: application/json" \
  -d "{\"pr_url\":\"https://github.com/OWNER/REPO/pull/NUMBER\"}"
```

```bash
curl -X POST http://localhost:8000/api/review/runs \
  -H "Content-Type: application/json" \
  -d "{\"context_id\":\"CONTEXT_ID\",\"pr_context\":{...}}"
```

Server-Sent Events:

```js
const events = new EventSource("http://localhost:8000/api/review/runs/RUN_ID/events")
```

Events use:

```json
{ "event_id": "...", "run_id": "...", "type": "run.completed", "sequence": 1, "created_at": "...", "payload": {} }
```

## Verification

```bash
cd server && npm test
cd server && npm run lint
cd frontend && npm run build
```

## Git Workflow

- `main`: never push directly. Use PRs and team review.
- `Liziark`: direct push is allowed. Develop here first, then open a PR to `main`.
- PR descriptions must include what changed, what was implemented, what was fixed, tests, and verification.
