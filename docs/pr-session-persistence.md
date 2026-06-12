# PR Session Persistence

PR Copilot persists PR context, review run state, events, and results to the
filesystem so they survive backend restarts.  This document covers the on-disk
layout, what is authoritative vs derived, schema versioning, operational
procedures, and deployment constraints.

---

## Directory Layout

All durable state lives under `{PR_COPILOT_STORAGE_DIR}/sessions/pr/`
(default `~/.pr-copilot/sessions/pr/`).

```
sessions/pr/
  {pr_key}/                          # PR session directory
    pr.json                          # PRSessionMeta (authoritative)
    index.json                       # PRSessionIndex (derived cache)
    runs/
      {run_id}/
        run.json                     # RunState (authoritative)
        context.json                 # ContextRecord (authoritative)
        task-plan.json               # TaskPlanRecord (authoritative)
        events.jsonl                 # PersistedEvent lines (authoritative)
        result.json                  # ResultRecord (authoritative)
        agent-sessions.json          # AgentSessionsRecord (authoritative)
```

### PR Key Format

The `pr_key` directory name is:

```
{owner}__{repo}__{pull_number}__{sha256_hash[:8]}
```

where the hash is `SHA256(f"{owner}/{repo}/{pull_number}")[:8]`.  All
non-alphanumeric characters in owner/repo are replaced with hyphens.  This
makes the directory name filesystem-safe and deterministic for a given PR
identity.

### Run ID Format

Run IDs are generated as `run_{uuid4_hex[:12]}` and are unique across all PR
sessions.

---

## Authoritative vs Derived Files

| File | Type | Description |
|------|------|-------------|
| `pr.json` | **Authoritative** | PR session metadata. Created once, updated on new runs. |
| `index.json` | **Derived cache** | Rebuildable index of all runs for this PR. Can be deleted and will be regenerated. |
| `run.json` | **Authoritative** | Run lifecycle state. Updated atomically on each transition. |
| `context.json` | **Authoritative** | Bounded PR context snapshot. Written once at run creation. |
| `task-plan.json` | **Authoritative** | Task plan and evidence. Written once at run creation. |
| `events.jsonl` | **Authoritative** | Append-only event log. Each line is a JSON `PersistedEvent`. |
| `result.json` | **Authoritative** | Final review result. Written once when the run reaches a terminal state. |
| `agent-sessions.json` | **Authoritative** | References to agent memory sessions. Written when agent sessions are registered. |

**Rule of thumb**: If you can rebuild it from other files, it's derived.
`index.json` is the only derived file; everything else is authoritative.

---

## Schema Versioning

Every persisted JSON document includes a `schema_version` field (currently
version 1 for all models).  The version is checked on load:

- **Same or older version**: Loaded normally.
- **Newer version**: Raises `UnsupportedSchemaError`.  The run remains
  discoverable via the inspection API, but the document cannot be hydrated.
  The API returns `context_status: "unsupported_version"` with a reason.

When adding new fields to a model:

1. Increment the schema version constant in `models.py`.
2. Make new fields optional with sensible defaults in `from_dict()`.
3. The `to_dict()` method always writes the current version.

---

## Single-Writer Constraint

`FilePRSessionStore` uses process-local `asyncio.Lock` instances per PR
session and per run.  This prevents concurrent writes within a single process.

**The store does not support multi-process or multi-host writes.**  If you
deploy multiple FastAPI workers, each worker will have its own lock pool and
writes may corrupt.  Run a single worker process, or externalise the store
(e.g. to a database) before scaling horizontally.

---

## Lifecycle Transitions

A run progresses through these states:

```
QUEUED → RUNNING → COMPLETED
                  → FAILED
                  → CANCELLING → CANCELLED
QUEUED → FAILED              (workspace prep failure)
RUNNING → INTERRUPTED        (backend crash, detected at startup)
```

Each transition is persisted atomically via `os.replace()` (temp file +
rename).  The `completed_at` timestamp is set on all terminal transitions.

---

## Startup Recovery

On backend startup, `recover_on_startup()` scans all PR sessions:

1. **Terminal runs** (completed/failed/cancelled): Restored into the in-memory
   `RunManager` with their final result and retained events.  PRContext is
   hydrated from `context.json` if the schema version is compatible.

2. **Non-terminal runs** (queued/running/cancelling): Atomically transitioned
   to `interrupted`.  The `completed_at` timestamp is set.

3. **Corrupt files**: Logged and counted in the `RecoveryReport`.  The run is
   skipped.

4. **Missing indexes**: Rebuilt automatically.

The recovery report is logged at INFO level.

---

## Operational Procedures

### Backup

The entire `sessions/pr/` directory can be backed up with standard file copy
tools.  Since writes are atomic (temp + rename), a point-in-time snapshot is
consistent as long as you copy the directory tree in one pass.

```bash
# Example: rsync backup
rsync -av ~/.pr-copilot/sessions/pr/ /backup/pr-copilot-sessions/
```

### Restore

To restore from backup:

1. Stop the backend.
2. Replace the `sessions/pr/` directory with the backup.
3. Start the backend.  Recovery will automatically rebuild any missing indexes.

### Rebuild Indexes

Indexes are derived caches.  To force-rebuild all indexes:

```python
from backend.storage.pr_session.store import FilePRSessionStore

store = FilePRSessionStore("~/.pr-copilot")
for pr_dir in store._sessions_root.iterdir():
    if not pr_dir.is_dir():
        continue
    pr_meta_file = pr_dir / "pr.json"
    if pr_meta_file.exists():
        import json
        meta = json.loads(pr_meta_file.read_text())
        pr_session_id = meta["pr_session_id"]
        store.rebuild_index(pr_session_id)
        print(f"Rebuilt index for {pr_session_id}")
```

Or simply restart the backend — recovery rebuilds missing indexes
automatically.

### Retention Dry-Run

To see what the retention policy would clean up without actually deleting
anything:

```python
from backend.storage.pr_session.retention import (
    load_retention_policy_from_env,
    plan_cleanup,
)
from backend.storage.pr_session.store import FilePRSessionStore

store = FilePRSessionStore("~/.pr-copilot")
policy = load_retention_policy_from_env()
plan = plan_cleanup(store, policy)

print(f"Files to delete: {len(plan.files)}")
print(f"Estimated bytes: {plan.estimated_bytes}")
for f in plan.files:
    print(f"  {f['type']}: {f['path']} ({f['age_days']}d old)")
```

Set retention periods via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `PR_COPILOT_RETENTION_RESULT_DAYS` | 90 | Days to keep result.json |
| `PR_COPILOT_RETENTION_EVENT_DAYS` | 30 | Days to keep events.jsonl |
| `PR_COPILOT_RETENTION_CONTEXT_DAYS` | 14 | Days to keep context.json |
| `PR_COPILOT_RETENTION_TRANSCRIPT_DAYS` | 7 | Days to keep agent transcripts |
| `PR_COPILOT_RETENTION_EMPTY_SESSION_DAYS` | 1 | Days to keep empty PR sessions |

### Safe Manual Inspection

Use the inspection API endpoints to examine persisted state without modifying
it:

```bash
# List runs for a PR
curl http://localhost:8000/api/inspection/pr-sessions/{owner}/{repo}/{pull_number}/runs

# Get run details
curl http://localhost:8000/api/inspection/runs/{run_id}

# Get persisted events (paginated)
curl "http://localhost:8000/api/inspection/runs/{run_id}/events?after_sequence=0&limit=50"

# Search findings across runs
curl "http://localhost:8000/api/inspection/pr-sessions/{owner}/{repo}/{pull_number}/findings?severity=high"

# Get agent session references
curl http://localhost:8000/api/inspection/runs/{run_id}/agent-sessions
```

For direct file inspection, the JSON files are human-readable.  The
`events.jsonl` file has one JSON object per line; use `jq` or similar tools
to filter:

```bash
# Show all tool-call events
jq 'select(.event_type == "tool.call")' events.jsonl

# Show the last 10 events
tail -10 events.jsonl | jq .
```

### Integrity Scanning

To check a PR session for corrupt or missing files:

```python
from backend.storage.pr_session.retention import scan_integrity
from backend.storage.pr_session.store import FilePRSessionStore

store = FilePRSessionStore("~/.pr-copilot")
report = scan_integrity(store, pr_session_id)

for finding in report.findings:
    print(f"[{finding.level.value}] {finding.message}")
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PR_COPILOT_STORAGE_DIR` | `~/.pr-copilot` | Root of the storage tree |
| `PR_COPILOT_RETENTION_RESULT_DAYS` | 90 | Result retention period |
| `PR_COPILOT_RETENTION_EVENT_DAYS` | 30 | Event retention period |
| `PR_COPILOT_RETENTION_CONTEXT_DAYS` | 14 | Context retention period |
| `PR_COPILOT_RETENTION_TRANSCRIPT_DAYS` | 7 | Transcript retention period |
| `PR_COPILOT_RETENTION_EMPTY_SESSION_DAYS` | 1 | Empty session retention |
