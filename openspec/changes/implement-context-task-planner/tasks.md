## 1. Data Model

- [x] 1.1 Create ContextTask, TaskSource, TaskTarget, TaskBudget, TaskRoute, AgentDefinition, and TaskPlan response structures
- [x] 1.2 Define constants for seven task types, accepted priority values, default budgets, route keys, and output schemas
- [x] 1.3 Add stable task id and deduplication identity helpers

## 2. Route And Agent Metadata

- [x] 2.1 Implement static Task Route registry for all seven task types
- [x] 2.2 Implement static Agent Definition registry for all seven context agent types
- [x] 2.3 Ensure all route metadata uses read-only tools only
- [x] 2.4 Ensure SubAgent route metadata disallows recursive task tool usage

## 3. Planner Rules

- [x] 3.1 Build the planner entrypoint that accepts PRContext and generated Evidence
- [x] 3.2 Generate `test_context` tasks for source-without-tests and related test lookup signals
- [x] 3.3 Generate `reference_context` tasks for source files and changed symbols or filename-derived queries
- [x] 3.4 Generate `security_context` tasks for security evidence and auth/payment risk hints
- [x] 3.5 Generate `config_context` tasks for config files, config risk hints, dependency files, and CI/checks inspection
- [x] 3.6 Generate `data_context` tasks for db path, migration, schema, model, SQL, and data access signals
- [x] 3.7 Generate `runtime_context` tasks for reliability, exception handling, dynamic execution, async, timeout, retry, and resource lifecycle signals
- [x] 3.8 Generate `patch_deep_dive` tasks for high-priority files not already covered by more specific tasks
- [x] 3.9 Keep every generated task self-contained with source, target, queries, budget, expected output, fallback, and pending status

## 4. Store Behavior

- [x] 4.1 Implement deterministic task deduplication
- [x] 4.2 Implement deterministic sorting by priority, task type, target file, and task id
- [x] 4.3 Implement summary counts by task type and priority
- [x] 4.4 Cap generated tasks per type to avoid oversized plans for large PRs

## 5. API Integration

- [x] 5.1 Add `POST /api/review/context-tasks` request and response handling
- [x] 5.2 Return 404 for missing `context_id`
- [x] 5.3 Register the Context Task Planner route in the FastAPI app through the existing review pipeline router
- [x] 5.4 Ensure the endpoint does not execute repository search, tests, CI/CD, shell commands, models, TaskTool, or SubAgents

## 6. Tests

- [x] 6.1 Add tests for required ContextTask fields and pending status
- [x] 6.2 Add tests for all seven task type categories and route metadata
- [x] 6.3 Add tests for source binding from evidence ids, rule ids, signals, and file facts
- [x] 6.4 Add tests for self-contained targets, queries, budgets, expected outputs, and fallbacks
- [x] 6.5 Add tests for deterministic deduplication, sorting, and summary counts
- [x] 6.6 Add API tests for successful plan generation and missing context errors
- [x] 6.7 Run the backend test suite and fix regressions
