from __future__ import annotations

from typing import Any

from backend.agent.runtime.agent_def import AgentDefinition, AgentRegistry
from backend.agent.tools.protocol import Tool
from backend.agent.tools.repo_context.models import RepoContextSession, TaskBudget

# --- 1.2 Shared read-only base prompt ---

_BASE_PROMPT = """\
You are a read-only context subagent. Your job is to gather evidence from the \
repository using the provided RepoContext Lite tools.

BEHAVIOR CONSTRAINTS:
- You MUST NOT edit, patch, or delete any repository files.
- You MUST NOT run shell commands, git commands, or any system commands.
- You MUST NOT submit review comments or interact with GitHub.
- You MUST NOT delegate tasks to other subagents or call task_tool/sub_agent.
- You only read and search. Your output is structured evidence.

WORKFLOW:
1. Call todo_write to plan your investigation steps.
2. Call verify_repo_context to confirm the workspace matches the PR.
3. Use the available search and read tools to gather evidence.
4. When done, call finish_context_package with your findings.
"""

# --- 1.3 Per-agent focus sections ---

_TEST_CONTEXT_FOCUS = """\
FOCUS: Test context analysis
Your goal is to find related test files, identify test gaps, and assess test \
coverage signals for the changed source files.
- Search for test files related to the changed source files.
- Identify source files that have no corresponding test changes.
- Look for test patterns, fixtures, and mocking conventions.
- Report test coverage gaps and suggest where tests should be added.
"""

_REFERENCE_CONTEXT_FOCUS = """\
FOCUS: Reference and caller analysis
Your goal is to find references, callers, API usage sites, and symbol impact \
for the changed files.
- Search for imports, function calls, and class references to changed symbols.
- Identify callers and consumers of changed APIs.
- Look for documentation references and configuration entries.
- Report the blast radius of the changes.
"""

_SECURITY_CONTEXT_FOCUS = """\
FOCUS: Security evidence analysis
Your goal is to inspect authentication, authorization, secrets, SQL risk, \
injection patterns, and input validation context.
- Search for hardcoded secrets, tokens, passwords, and API keys.
- Look for SQL construction patterns (concatenation, format strings).
- Check for dangerous execution patterns (eval, exec, subprocess).
- Inspect auth/permission-related code paths.
- Verify input validation and sanitization patterns.
"""

_CONFIG_CONTEXT_FOCUS = """\
FOCUS: Configuration and CI/CD analysis
Your goal is to inspect configuration files, environment variables, dependency \
files, CI/checks, and deployment context.
- Read and analyze changed configuration files.
- Check dependency files for version changes and new dependencies.
- Look for CI/CD workflow files and check configurations.
- Inspect environment variable usage and .env patterns.
- Report deployment and infrastructure impact.
"""

_DATA_CONTEXT_FOCUS = """\
FOCUS: Data and schema analysis
Your goal is to inspect database schemas, migrations, models, cache patterns, \
and data access context.
- Search for migration files and schema changes.
- Look for ORM model definitions and relationships.
- Check for data access patterns and query construction.
- Inspect cache usage and invalidation patterns.
- Report data integrity and migration risks.
"""

_RUNTIME_CONTEXT_FOCUS = """\
FOCUS: Runtime behavior analysis
Your goal is to inspect exception handling, async behavior, concurrency, \
timeouts, retries, and resource lifecycle.
- Search for bare except clauses and error handling patterns.
- Look for async/await usage and potential race conditions.
- Check for timeout, retry, and circuit breaker patterns.
- Inspect resource lifecycle (open/close, connect/disconnect).
- Report runtime risk patterns.
"""

_PATCH_DEEP_DIVE_FOCUS = """\
FOCUS: Patch deep-dive analysis
Your goal is to perform detailed inspection of complex or high-priority patches.
- Read the full patch hunks for the target files.
- Analyze complexity, edge cases, and implementation patterns.
- Look for potential bugs, off-by-one errors, and logic issues.
- Check for error handling completeness in new code paths.
- Report implementation risks and improvement suggestions.
"""

# --- Prompt map ---

_PROMPT_MAP: dict[str, str] = {
    "test-context-agent": _BASE_PROMPT + _TEST_CONTEXT_FOCUS,
    "reference-context-agent": _BASE_PROMPT + _REFERENCE_CONTEXT_FOCUS,
    "security-context-agent": _BASE_PROMPT + _SECURITY_CONTEXT_FOCUS,
    "config-context-agent": _BASE_PROMPT + _CONFIG_CONTEXT_FOCUS,
    "data-context-agent": _BASE_PROMPT + _DATA_CONTEXT_FOCUS,
    "runtime-context-agent": _BASE_PROMPT + _RUNTIME_CONTEXT_FOCUS,
    "patch-deep-dive-agent": _BASE_PROMPT + _PATCH_DEEP_DIVE_FOCUS,
}

# --- Tool allowlists (mirrored from planner) ---

_BASE_TOOLS = ["todo_write", "verify_repo_context", "finish_context_package"]

_AGENT_ALLOWED_TOOLS: dict[str, list[str]] = {
    "test-context-agent": _BASE_TOOLS + ["read_file_patch", "search_diff", "search_tests_for", "read_repo_file"],
    "reference-context-agent": _BASE_TOOLS + ["read_file_patch", "search_diff", "search_repo", "read_repo_file"],
    "security-context-agent": _BASE_TOOLS + ["read_file_patch", "search_diff", "search_repo", "read_repo_file", "read_repo_manifest"],
    "config-context-agent": _BASE_TOOLS + ["search_diff", "search_repo", "read_repo_file", "read_repo_manifest", "read_check_summary"],
    "data-context-agent": _BASE_TOOLS + ["read_file_patch", "search_diff", "search_repo", "read_repo_file"],
    "runtime-context-agent": _BASE_TOOLS + ["read_file_patch", "search_diff", "search_repo", "read_repo_file"],
    "patch-deep-dive-agent": _BASE_TOOLS + ["read_file_patch", "search_diff", "read_repo_file"],
}

_AGENT_DESCRIPTIONS: dict[str, str] = {
    "test-context-agent": "Finds related tests, test gaps, and test coverage signals for changed source files",
    "reference-context-agent": "Finds references, callers, API usage, and symbol impact for changed files",
    "security-context-agent": "Inspects authentication, authorization, secrets, SQL risk, and input validation context",
    "config-context-agent": "Inspects configuration, environment variables, dependency files, CI/checks, and deployment context",
    "data-context-agent": "Inspects database, schema, migration, cache, model, and data access context",
    "runtime-context-agent": "Inspects exception handling, async behavior, concurrency, timeouts, retries, and resource lifecycle",
    "patch-deep-dive-agent": "Performs deep local inspection of high-priority or complex patches",
}

_DISALLOWED_TOOLS = ["task_tool", "sub_agent"]

DEFAULT_MAX_STEPS = 5


# --- 1.4 Registry builder ---

def build_default_subagent_registry() -> AgentRegistry:
    """Build an AgentRegistry with all seven context subagent definitions."""
    registry = AgentRegistry()
    for agent_type, prompt in _PROMPT_MAP.items():
        registry.register(AgentDefinition(
            name=agent_type,
            description=_AGENT_DESCRIPTIONS[agent_type],
            system_prompt=prompt,
            default_max_steps=DEFAULT_MAX_STEPS,
            allowed_tools=list(_AGENT_ALLOWED_TOOLS[agent_type]),
            disallowed_tools=list(_DISALLOWED_TOOLS),
        ))
    return registry


# --- 4.1 Per-task RepoContext session factory ---

def build_context_child_tools(
    child_session_id: str,
    task: dict[str, Any] | None = None,
    *,
    context_id: str = "",
    repo_root: str = "",
    pr_context: Any = None,
) -> list[Tool]:
    """Build RepoContext Lite tools for a child subagent session.

    Creates a fresh RepoContextSession per task so sibling subagents
    do not share todos, budget usage, or final_package.
    """
    from backend.agent.tools.repo_context.tool_defs import create_context_tools

    task_id = ""
    budget = TaskBudget()

    if task is not None:
        task_id = task.get("task_id", "")
        task_budget = task.get("budget")
        if isinstance(task_budget, dict):
            budget = TaskBudget(
                max_searches=task_budget.get("max_searches", 5),
                max_files=task_budget.get("max_files", 10),
                max_tokens=task_budget.get("max_tokens", 3000),
            )

    session = RepoContextSession(
        context_id=context_id or child_session_id,
        task_id=task_id or child_session_id,
        repo_root=repo_root,
        budget=budget,
    )

    return create_context_tools(session, pr_context)
