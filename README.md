# PR Copilot

AI-powered pull request review assistant.

## Project Structure

```
backend/        FastAPI application
  main.py       App entry point with /api/health endpoint
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

## Scope

This milestone is a **minimal app shell only**. It does not include:

- GitHub PR fetching or integration
- Diff parsing
- AI model calls
- Agent orchestration
- Review report generation
- Authentication, persistence, deployment, or CI setup
