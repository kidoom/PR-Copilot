# PR Copilot

AI-powered pull request review assistant.

## Project Structure

```
backend/        FastAPI application
  main.py       App entry point with /api/health endpoint
  github/       GitHub PR URL parsing and API client
  pr_context/   PR context builder, diff parser, classifier, scorer, and API routes
  requirements.txt  Python dependencies
frontend/       Static web console
  index.html    HTML entry point
  style.css     Styles
  app.js        Connectivity-check logic
```

## Local Setup

**Prerequisites:** Python 3.10+

```bash
# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install backend dependencies
pip install -r backend/requirements.txt
```

## Run

```bash
# Start the backend (from project root)
uvicorn backend.main:app --reload --port 8000
```

Open `frontend/index.html` in a browser (double-click or serve it). The page will call `http://localhost:8000/api/health` and display the result.

## PR Context API

This milestone adds the backend PR context workflow. It accepts a GitHub pull request URL, fetches PR metadata, commits, and changed files, then builds analysis-ready context views.

### Create PR Context

```bash
curl -X POST http://localhost:8000/api/pr/context \
  -H "Content-Type: application/json" \
  -d "{\"pr_url\":\"https://github.com/OWNER/REPO/pull/NUMBER\",\"github_token\":\"OPTIONAL_TOKEN\"}"
```

The response returns an overview with PR metadata, commits, changed files, file classifications, priority scores, and a `context_id`.

### Inspect Patch Index

```bash
curl http://localhost:8000/api/pr/context/CONTEXT_ID/patch-index
```

### Inspect One File Patch

```bash
curl "http://localhost:8000/api/pr/context/CONTEXT_ID/files/PATH/TO/FILE/patch?max_lines=500"
```

## Scope

Included:

- GitHub PR URL parsing
- GitHub API client with optional token authentication
- PR metadata, commit, and changed-file normalization
- Patch hunk parsing with line number mapping
- File classification and priority scoring
- Edge case handling for large, binary, generated, renamed, deleted, and no-patch files
- In-memory PR context store and API views

Not included yet:

- AI model calls
- Agent orchestration
- Review report generation
- Authentication, persistence, deployment, or CI setup
