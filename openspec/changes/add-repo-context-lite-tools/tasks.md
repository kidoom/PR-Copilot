## 1. Module Structure And Data Models

- [x] 1.1 Create `backend/repo_context/` package with service and tool modules
- [x] 1.2 Add `RepoContextSession` data model for context id, task id, repo root, verification state, budget usage, and final package state
- [x] 1.3 Add `RepoVerificationState`, `ToolUsage`, `ContextEvidencePackage`, `ContextFinding`, and `ContextEvidenceRef` models
- [x] 1.4 Add constants for ignored directories, sensitive path patterns, maximum search results, maximum snippet lines, and maximum snippet bytes

## 2. Policy And Safety

- [ ] 2.1 Implement repository path resolution that rejects traversal and paths outside the verified repository root
- [ ] 2.2 Implement ignored-directory filtering for search and file reads
- [ ] 2.3 Implement sensitive-file detection that blocks or summarizes raw content from secret-bearing paths
- [ ] 2.4 Implement budget tracking for search count, file read count, and approximate token output
- [ ] 2.5 Implement verification gating so repository-content tools fail until repo context is verified

## 3. RepoContext Lite Tools

- [ ] 3.1 Implement `verify_repo_context` with local workspace owner/repo/head-sha verification and cached session state
- [ ] 3.2 Implement `read_file_patch` using existing `PRContext` patch access without requiring repository verification
- [ ] 3.3 Implement `search_diff` over parsed PR hunks with skipped-file metadata
- [ ] 3.4 Implement `search_repo` with bounded query, path scope, ignored-directory filtering, and structured match output
- [ ] 3.5 Implement `read_repo_file` with safe path checks, bounded line ranges, truncation metadata, and structured errors
- [ ] 3.6 Implement `search_tests_for` using source-file naming conventions and test path heuristics
- [ ] 3.7 Implement `read_repo_manifest` for README, dependency files, CODEOWNERS, CI workflows, and rule files
- [ ] 3.8 Implement `read_check_summary` as a bounded unavailable/placeholder result if GitHub Checks integration is not present
- [ ] 3.9 Implement `finish_context_package` validation and session recording for final structured SubAgent output
- [ ] 3.10 Add or adapt `todo_write` for context SubAgent sessions with one-in-progress validation

## 4. Tool Registration And Filtering

- [ ] 4.1 Register RepoContext Lite tools with the existing agent runtime tool registry
- [ ] 4.2 Ensure model schema projection excludes runtime-only metadata
- [ ] 4.3 Add per-tool metadata for risk level, read-only behavior, concurrency safety, budget cost, and verification requirement
- [ ] 4.4 Add helper for building task-scoped context tools from a `ContextTask`, `PRContext`, and `RepoContextSession`
- [ ] 4.5 Ensure child/context agents cannot access recursive `task_tool` or `sub_agent` tools

## 5. Planner Route Metadata

- [ ] 5.1 Update `context_task_planner.py` route metadata from one shared read-only list to per-task allowlists
- [ ] 5.2 Add `todo_write` and `finish_context_package` to all seven context agent routes
- [ ] 5.3 Ensure `patch_deep_dive` route does not include `search_repo`
- [ ] 5.4 Update agent definition metadata to match the route allowlists and deny recursive task tools

## 6. Tests

- [ ] 6.1 Add unit tests for RepoContext Lite tool schema and runtime metadata
- [ ] 6.2 Add unit tests for verification success, verification failure, and verification-gated tools
- [ ] 6.3 Add unit tests for safe path handling, ignored directories, large-file truncation, and sensitive-file blocking
- [ ] 6.4 Add unit tests for search and read budget exhaustion
- [ ] 6.5 Add unit tests for `search_repo`, `read_repo_file`, `search_tests_for`, `read_repo_manifest`, and `search_diff` using fake repositories and PR contexts
- [ ] 6.6 Add unit tests for `finish_context_package` required fields, bounded status values, evidence references, and usage recording
- [ ] 6.7 Update context task planner tests for per-agent allowlists
- [ ] 6.8 Run the backend test suite and fix regressions

## 7. Documentation

- [ ] 7.1 Document RepoContext Lite tool contracts and safety boundaries in backend docs or README
- [ ] 7.2 Document how context SubAgents should use `todo_write`, RepoContext Lite tools, and `finish_context_package`
- [ ] 7.3 Update data structure documentation with `ContextEvidencePackage` and tool usage fields
