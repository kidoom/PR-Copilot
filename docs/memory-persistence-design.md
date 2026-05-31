# Memory Persistence Design

**Status**: Draft  
**Scope**: Agent session memory persistence for main agent and subagents

## 1. Core Decision

Session persistence should not distinguish between local mode and cloud mode in code.

The rule is:

```text
Agent session memory is persisted in the backend runtime's configured storage_dir.
```

If the backend runs on the user's machine, memory is stored on that machine. If the backend runs on a server, memory is stored on that server. The deployment location changes, but the persistence abstraction does not.

The implementation should avoid logic like:

```text
if local:
  store locally
if cloud:
  store remotely
```

Instead, the backend should use one configurable storage root:

```text
PR_COPILOT_STORAGE_DIR=.pr-copilot
```

or in server deployment:

```text
PR_COPILOT_STORAGE_DIR=/var/lib/pr-copilot
```

## 2. What Gets Persisted

Agent memory persistence stores durable execution state for agent sessions:

- Main agent messages
- Subagent messages
- Compact summaries
- Todo state
- Tool observations that must remain auditable
- Final evidence package entries
- Run/session metadata

This is separate from repository workspaces.

```text
MemorySession:
  durable agent conversation and working state

RepoWorkspace:
  temporary repository checkout or local repo binding
```

Repo workspaces may be deleted after a run. Memory sessions should remain available for debugging, resume, audit, and future compaction.

## 3. Session Scope

Main agent and subagents reuse the same memory mechanism, but each agent gets an isolated session.

```text
MemoryRuntime
  - run_123_main
  - run_123_task_a_security_context_agent
  - run_123_task_b_test_context_agent
  - run_123_task_c_runtime_context_agent
```

Shared mechanism:

- Message schema
- Append/load behavior
- Summary persistence
- Todo persistence
- Compression hooks
- Safe tool-pair repair

Isolated state:

- Messages
- Todos
- Summaries
- Tool observations
- Budget usage
- Final context/evidence package

Hard rule:

```text
Each agent only writes to its own MemorySession.
```

Main agent sessions store orchestration and synthesis state. Subagent sessions store task-local evidence gathering state. The main agent may store TaskTool results, subagent status, and evidence package summaries, but it must not persist complete subagent internal transcripts in the main session.

## 4. Session Creation

Creating a new session is a first-class capability. The runtime should not append to an implicit global transcript.

```python
class MemorySessionMeta:
    session_id: str
    run_id: str
    agent_kind: Literal["main", "subagent"]
    agent_type: str
    context_id: str
    task_id: str | None = None
    created_at: str
    updated_at: str
    message_count: int = 0
    event_count: int = 0
    summary_count: int = 0
    evidence_package_count: int = 0
```

Main session example:

```json
{
  "session_id": "run_123_main",
  "run_id": "run_123",
  "agent_kind": "main",
  "agent_type": "main-agent",
  "context_id": "ctx_abc"
}
```

Subagent session example:

```json
{
  "session_id": "run_123_task_a_security_context_agent",
  "run_id": "run_123",
  "agent_kind": "subagent",
  "agent_type": "security-context-agent",
  "task_id": "task_a",
  "context_id": "ctx_abc"
}
```

Session id should be filesystem-safe:

```text
^[A-Za-z0-9_-]{1,120}$
```

Agent types can keep their canonical hyphenated names in metadata, while session ids can use underscore-normalized forms.

## 5. MVP Storage Layout

For MVP, use file-based persistence under the backend storage directory.

```text
.pr-copilot/
  memory/
    main/
      run_123_main/
        transcript.jsonl
        state.json

    subagents/
      security-context-agent/
        run_123_task_a_security_context_agent/
          transcript.jsonl
          state.json

      test-context-agent/
        run_123_task_b_test_context_agent/
          transcript.jsonl
          state.json

      runtime-context-agent/
        run_123_task_c_runtime_context_agent/
          transcript.jsonl
          state.json

  workspaces/
    temp-clones/
```

Suggested file responsibilities:

| File | Purpose |
|---|---|
| `transcript.jsonl` | Append-only transcript entries for messages, summaries, todos, events, and evidence packages |
| `state.json` | Session status, run id, context id, agent type, task id, timestamps, and counters |

This layout intentionally makes main-agent and subagent memory physically visible:

```text
memory/main/
memory/subagents/{agent_type}/
```

It also avoids one massive flat transcript directory as subagent sessions grow.

## 6. Transcript Entry Types

`transcript.jsonl` should use typed append-only entries similar to Kgent/CC-style persistence.

```text
message
agent_event
session_meta
summary
todo_state
evidence_package
```

Each line is a single JSON object:

```json
{
  "entry_id": "evt_xxx",
  "session_id": "run_123_main",
  "type": "message",
  "created_at": "2026-05-30T00:00:00Z",
  "schema_version": 1,
  "payload": {}
}
```

Only `message` entries and summary boundary messages are hydrated into model context. Other entry types are durable structured state for audit, resume, UI, and later compression.

## 7. Message Roles

`TranscriptEntry.type` is not the same thing as model message role.

`type="message"` entries contain model messages with roles:

```text
system
user
assistant
tool
```

Recommended message shape:

```python
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[ContentBlock]
    assistant_text: str = ""
    is_meta: bool = False
```

Tool calls must preserve pairing:

```text
assistant tool_use.id
  -> tool tool_result.tool_use_id
```

Safe trim and compaction must not break these pairs.

System prompts should usually be stored as `session_meta` with prompt version/profile rather than repeatedly appended as ordinary messages. The runtime/context builder can inject the correct system prompt when calling the model.

## 8. Interface Shape

The runtime should depend on a store interface rather than direct file writes.

```python
class MemoryStore:
    def create_session(self, meta: MemorySessionMeta) -> None: ...
    def get_session(self, session_id: str) -> MemorySessionMeta | None: ...
    def append_message(self, session_id: str, message: Message) -> None: ...
    def append_event(self, session_id: str, event: dict) -> None: ...
    def append_summary(self, session_id: str, summary: dict) -> None: ...
    def append_todo_state(self, session_id: str, todos: dict) -> None: ...
    def append_evidence_package(self, session_id: str, package: dict) -> None: ...
    def load_entries(self, session_id: str) -> list[TranscriptEntry]: ...
    def hydrate_messages(self, session_id: str) -> list[Message]: ...
    def list_sessions(
        self,
        *,
        agent_kind: str | None = None,
        agent_type: str | None = None,
    ) -> list[MemorySessionMeta]: ...
```

Initial implementation:

```text
FileMemoryStore
```

Future implementations:

```text
SQLiteMemoryStore
PostgresMemoryStore
ObjectStorageArtifactStore
```

## 9. Hydration Behavior

When a session is loaded:

1. Read `transcript.jsonl`.
2. Iterate entries in order.
3. Append `message` entries into the model message list.
4. When a `summary` entry appears, replace older model messages with `[summary boundary] + recent_messages`.
5. Ignore `agent_event`, `session_meta`, `todo_state`, and `evidence_package` for model hydration unless a later context builder explicitly attaches them.
6. Repair tool-use/tool-result pairing.
7. Return a `MemorySession` to the agent runtime.

The important behavior is:

```text
summary boundary replaces older transcript for model context,
but full transcript remains auditable on disk.
```

## 10. Relationship To RunManager

`RunManager` owns active run lifecycle:

- queued/running/completed/failed/cancelled
- WebSocket events
- background task handle
- cancellation state

`MemoryStore` owns durable session state:

- messages
- summaries
- todos
- compact state
- evidence package entries

`RunManager` should write to memory through `MemorySession` or `MemoryStore`, but should not directly manipulate transcript files.

## 11. Open Questions

- Should each subagent session be kept forever, or retained only through its final evidence package after the run?
- Should full tool outputs be persisted in `transcript.jsonl`, or should large outputs be moved to artifact files and referenced by id?
- Should completed runs have a retention TTL in MVP?
- Should `session_meta` include system prompt text or only prompt profile/version?
- Should subagent session directories be grouped by `agent_type` only, or also by `run_id` for easier cleanup?

