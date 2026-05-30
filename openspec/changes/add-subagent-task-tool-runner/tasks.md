## 1. Task Tool Interface

- [x] 1.1 Extend `TaskTool` to expose a Tool-compatible `name`, `description`, `input_schema`, `risk_level`, `is_read_only`, `is_concurrency_safe`, and async `call()` interface.
- [x] 1.2 Preserve the existing `TaskTool.run()` helper and existing task-payload prompt fallback behavior.
- [x] 1.3 Clamp `max_steps` in the task tool and return clear errors for empty prompts or unknown agent types.

## 2. Subagent Harness

- [x] 2.1 Add child session id generation for isolated subagent runs.
- [x] 2.2 Implement `run_subagent` to build fresh child messages from the child system prompt and delegated user prompt.
- [x] 2.3 Make `run_subagent` call the existing `run_loop` with the filtered child tool registry.
- [x] 2.4 Return `SubAgentResult` metadata including child session id, agent type, output, steps, token usage, and max-step status.

## 3. Runner Closure and Child Tool Binding

- [x] 3.1 Add a runner builder that captures model, parent session id, agent registry, PR context, and parent RepoContext session data.
- [x] 3.2 Create a fresh child `RepoContextSession` for every subagent run, copying only safe fields such as `context_id`, `task_id`, `repo_root`, and budget.
- [x] 3.3 Build RepoContext Lite tools against the child session and PR context at run time.
- [x] 3.4 Register and filter child tools using the selected `AgentDefinition` allowlist and denylist.
- [x] 3.5 Ensure recursive delegation tools (`task`, `task_tool`, `sub_agent`) are unavailable to child agents.

## 4. Tests and Verification

- [x] 4.1 Add tests for `TaskTool.call()` delegation, error handling, agent type validation, and max-step clamping.
- [x] 4.2 Add tests proving `run_subagent` reuses `run_loop` with fresh system/user messages.
- [x] 4.3 Add tests proving child `todo_write` state does not mutate the parent RepoContext session.
- [x] 4.4 Add tests proving sibling subagents receive independent RepoContext sessions and final packages.
- [x] 4.5 Add tests proving child tool filtering retains allowed tools and removes recursive delegation tools.
- [x] 4.6 Run the agent runtime test suite and update docs if public runtime usage changes.
