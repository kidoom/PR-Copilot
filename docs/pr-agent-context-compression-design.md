# PR Agent Context Compression Design

**Status**: Draft  
**Scope**: Domain-specific context compression for PR review agents

## 1. Core Decision

PR-Copilot should reuse one shared memory/compression mechanism for main agent and subagents, while keeping each agent session isolated.

```text
Shared:
  Memory runtime
  Token estimation
  MicroCompact
  AutoCompact
  ReactiveCompact
  Manual/Resume Compact
  Safe tool-use/tool-result repair

Isolated:
  main agent session
  subagent sessions
```

This follows the Kgent-style compression model, but PR-Copilot needs PR-review-specific compact profiles. A generic conversation summary is not enough for PR review because the important state is evidence, task routing, repository context, and review progress.

## 2. Why PR Compression Needs Specialization

Kgent-style compaction preserves general working context:

- Current goal
- Completed steps
- Files read or modified
- Important facts
- Pending work

PR-Copilot must preserve a different set of durable facts:

- PR identity: `owner`, `repo`, `pull_number`, `head_sha`
- Current review run: `run_id`, `context_id`
- Planner output: `TaskPlan`, route metadata, pending/completed tasks
- Repository workspace: local or temp clone source, verified SHA
- Evidence collected by subagents
- Files searched/read and why they matter
- Inconclusive areas and blocked tasks
- Final context/evidence packages
- Main agent synthesis state

The compression layer should shrink message history, not erase server-owned state.

## 3. Non-Compressible Source Of Truth

These objects should not rely on model summaries as their only storage:

```text
RunManager.status
TaskPlan
RepoWorkspace
RepoContextSession.final_package
Todo state
Tool usage/budget counters
Run events
Final review result
```

Compaction may reference them, but the backend should keep structured versions separately.

Rule:

```text
Compress model conversation.
Do not compress away structured review state.
```

## 4. Compression Layers

### 4.1 Safe Trim

Safe trim keeps message lists valid for model APIs.

It must preserve tool call protocol integrity:

- Remove orphan `tool_result` blocks.
- Remove or convert orphan `tool_use` blocks.
- Keep recent tool-use/tool-result pairs together.

This is needed because main agent and subagents rely heavily on tools. A naive slice of recent messages can break model API requirements.

### 4.2 MicroCompact

MicroCompact should not mutate persisted session history. It should create a request-view copy before each model call and replace old large tool results with compact placeholders.

Kgent used this for generic file tools. PR-Copilot should specialize it for RepoContext tools:

```text
Compactable tool results:
  read_file_patch
  read_repo_file
  search_repo
  search_tests_for
  read_repo_manifest
  read_check_summary
```

Example replacement:

```text
[Old RepoContext tool result compacted:
 tool=read_repo_file,
 file=backend/auth/service.py,
 original_chars=18420,
 full content preserved in memory store/artifact]
```

Do not compact:

- Tool errors
- Recent tool results
- `finish_context_package`
- Small results below threshold
- Permission/cancellation observations

### 4.3 AutoCompact

AutoCompact runs before a model call when estimated request tokens approach the context window.

It should:

1. Estimate request tokens after context builder and MicroCompact.
2. Compare against:

```text
context_window_tokens - compact_max_summary_tokens - auto_compact_buffer_tokens
```

3. Summarize older session messages.
4. Rewrite the model-visible session to:

```text
[compact summary boundary] + recent valid messages
```

AutoCompact failure should be non-fatal. The agent can continue and rely on ReactiveCompact if the provider rejects the prompt.

### 4.4 ReactiveCompact

ReactiveCompact runs when the model provider returns a context length error.

It should:

1. Catch provider-specific context length exceptions.
2. Compact the current session.
3. Rebuild the request.
4. Retry the model call once.

This is the safety net for inaccurate token estimation or unusually large tool outputs.

### 4.5 Resume Compact

Resume Compact runs when a persisted session is hydrated before a new run continues.

It should:

1. Load persisted messages.
2. Build the same request view the model would see.
3. Apply MicroCompact.
4. Estimate size.
5. Compact before starting the run if oversized.

For PR-Copilot, this matters when a review run is resumed or when a user reopens a long-running review.

### 4.6 Manual Compact

Manual Compact is an operator/user-triggered endpoint or internal command.

It should be blocked while the session has an active run, because rewriting memory during an active model/tool loop can corrupt state.

## 5. Main Agent Compact Profile

The main agent compact prompt should preserve orchestration and synthesis state.

Required summary sections:

```markdown
## Current Review Run
- run_id, context_id, owner, repo, pull_number, head_sha

## User Objective
- Most recent explicit user request
- Review intent or constraints

## Planner State
- TaskPlan summary
- Task counts by type/priority
- Route mapping assumptions
- Pending/completed/failed task ids

## Main Agent Progress
- Decisions already made
- TaskTool calls made
- Subagent batches started/completed

## Subagent Results
- Evidence packages returned
- Inconclusive or blocked tasks
- Important warnings/errors

## Review Synthesis State
- Draft conclusions
- Risks already supported by evidence
- Questions still unresolved

## Pending Work
- Next immediate action
- What must not be repeated
```

Main compact must not invent findings. If evidence is absent, the summary should say the topic is unresolved.

## 6. Subagent Compact Profile

Subagent compact prompt should preserve task-local evidence gathering.

Required summary sections:

```markdown
## Delegated Task
- task_id, task_type, agent_type
- target files, symbols, queries, expected output
- budget limits

## Repository Context
- owner, repo, head_sha
- workspace source and verification status

## Work Completed
- searches performed
- files read
- patches inspected
- manifests/checks read

## Evidence Collected
- findings with file paths and line references when available
- related tests/references/config/data/runtime signals
- confidence and limitations

## Todo State
- completed/in-progress/pending todo items

## Final Package Status
- not_started / in_progress / submitted
- package status if submitted

## Pending Work
- next tool call or finalization step
```

Subagent compact should be narrower than main compact. It should not preserve sibling subagent history.

## 7. Session Layout

Compression should operate on isolated memory sessions:

```text
run_123:main
run_123:task_abc:security-context-agent
run_123:task_def:test-context-agent
```

All sessions use the same compression engine:

```text
estimate_messages_tokens()
repair_tool_message_pairs()
micro_compact_messages()
should_auto_compact()
execute_compact()
select_recent_messages()
```

The compact profile changes by session type:

```text
main profile
subagent profile
```

## 8. Runtime Integration Points

### Main Runner

Before main model calls:

```text
messages -> context builder -> MicroCompact -> AutoCompact check -> model call
```

On context length error:

```text
ReactiveCompact -> rebuild request -> retry
```

### Subagent Runner

Each subagent uses the same compression flow, but on its own session:

```text
subagent messages -> MicroCompact -> AutoCompact -> model call
```

### TaskTool

TaskTool should not own compression. It only starts subagents and returns results. Compression belongs to the runtime loop/memory layer.

### RunManager

RunManager should emit compact events for observability:

```text
compact.started
compact.retry
compact.completed
compact.failed
```

These events should not expose hidden reasoning or large raw tool outputs.

## 9. MVP Implementation Scope

Initial implementation should include:

- `CompressionConfig`
- token estimation
- safe tool-pair repair
- MicroCompact for RepoContext tool results
- AutoCompact before model calls
- ReactiveCompact on context length errors
- PR-specific compact prompts for main and subagent sessions
- summary boundary message
- memory-store summary persistence

Defer:

- provider-specific exact tokenizers
- cross-run long-term memory
- embedding-based retrieval of old summaries
- database-backed memory store
- token-level streaming aware compaction

## 10. Open Questions

- Should `finish_context_package` always be excluded from MicroCompact, or can very large packages be artifact-backed?
- Should compact summaries be generated by the same model as the run, or a cheaper dedicated summarizer model?
- Should subagent sessions be compacted after completion before returning to main agent?
- Should main agent receive full subagent outputs, or only structured evidence package summaries?
- Should compact events be visible in frontend timeline by default?

