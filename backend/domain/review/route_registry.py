"""Shared route-definition registry for task types.

Single source of truth for:
- Task types and route keys
- Agent types
- Tool allowlists
- Output schemas
- Budgets
- Maximum steps

Used by both the planner (context_task_planner) and the runtime (subagents).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# --- Base tool sets ---

_SUBAGENT_DISALLOWED_TOOLS = ["task_tool", "sub_agent"]

_BASE_TOOLS = ["todo_write", "verify_repo_context"]


# --- Route definition ---

@dataclass(frozen=True)
class RouteDefinition:
    """Immutable definition of a task route."""
    task_type: str
    route_key: str
    agent_type: str
    allowed_tools: tuple[str, ...]
    output_schema: dict[str, Any]
    max_steps: int = 5
    budget: dict[str, int] = field(default_factory=lambda: {"max_searches": 5, "max_files": 10, "max_tokens": 3000})
    is_default_lane: bool = False
    description: str = ""


# --- Output schemas ---

_OUTPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "test_context": {
        "type": "object",
        "properties": {
            "related_tests": {"type": "array", "items": {"type": "string"}},
            "test_gaps": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "string"},
        },
    },
    "reference_context": {
        "type": "object",
        "properties": {
            "references": {"type": "array", "items": {"type": "string"}},
            "callers": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "string"},
        },
    },
    "security_context": {
        "type": "object",
        "properties": {
            "findings": {"type": "array", "items": {"type": "string"}},
            "risk_level": {"type": "string"},
            "summary": {"type": "string"},
        },
    },
    "config_context": {
        "type": "object",
        "properties": {
            "config_files": {"type": "array", "items": {"type": "string"}},
            "ci_status": {"type": "string"},
            "summary": {"type": "string"},
        },
    },
    "data_context": {
        "type": "object",
        "properties": {
            "models": {"type": "array", "items": {"type": "string"}},
            "migrations": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "string"},
        },
    },
    "runtime_context": {
        "type": "object",
        "properties": {
            "risks": {"type": "array", "items": {"type": "string"}},
            "patterns": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "string"},
        },
    },
    "patch_deep_dive": {
        "type": "object",
        "properties": {
            "complexity_notes": {"type": "array", "items": {"type": "string"}},
            "suggestions": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "string"},
        },
    },
}


# --- Tool allowlists ---

_TOOL_ALLOWLISTS: dict[str, tuple[str, ...]] = {
    "test_context": tuple(_BASE_TOOLS + ["read_file_patch", "search_diff", "search_tests_for", "read_repo_file"]),
    "reference_context": tuple(_BASE_TOOLS + ["read_file_patch", "search_diff", "search_repo", "read_repo_file"]),
    "security_context": tuple(_BASE_TOOLS + ["read_file_patch", "search_diff", "search_repo", "read_repo_file", "read_repo_manifest"]),
    "config_context": tuple(_BASE_TOOLS + ["search_diff", "search_repo", "read_repo_file", "read_repo_manifest", "read_check_summary"]),
    "data_context": tuple(_BASE_TOOLS + ["read_file_patch", "search_diff", "search_repo", "read_repo_file"]),
    "runtime_context": tuple(_BASE_TOOLS + ["read_file_patch", "search_diff", "search_repo", "read_repo_file"]),
    "patch_deep_dive": tuple(_BASE_TOOLS + ["read_file_patch", "search_diff", "read_repo_file"]),
}


# --- Budgets ---

_BUDGETS: dict[str, dict[str, int]] = {
    "test_context": {"max_searches": 5, "max_files": 10, "max_tokens": 3000},
    "reference_context": {"max_searches": 5, "max_files": 10, "max_tokens": 3000},
    "security_context": {"max_searches": 4, "max_files": 8, "max_tokens": 2500},
    "config_context": {"max_searches": 3, "max_files": 6, "max_tokens": 2000},
    "data_context": {"max_searches": 4, "max_files": 8, "max_tokens": 2500},
    "runtime_context": {"max_searches": 4, "max_files": 8, "max_tokens": 2500},
    "patch_deep_dive": {"max_searches": 3, "max_files": 5, "max_tokens": 2000},
}


# --- Descriptions ---

_DESCRIPTIONS: dict[str, str] = {
    "test_context": "Finds related tests, test gaps, and test coverage signals for changed source files",
    "reference_context": "Finds references, callers, API usage, and symbol impact for changed files",
    "security_context": "Inspects authentication, authorization, secrets, SQL risk, and input validation context",
    "config_context": "Inspects configuration, environment variables, dependency files, CI/checks, and deployment context",
    "data_context": "Inspects database, schema, migration, cache, model, and data access context",
    "runtime_context": "Inspects exception handling, async behavior, concurrency, timeouts, retries, and resource lifecycle",
    "patch_deep_dive": "Performs deep local inspection of high-priority or complex patches",
}


# --- Default lanes ---

DEFAULT_LANES = frozenset({"test_context", "reference_context", "patch_deep_dive"})


# --- Route registry ---

ROUTE_REGISTRY: dict[str, RouteDefinition] = {}

for _tt in _OUTPUT_SCHEMAS:
    _agent_type = _tt.replace("_", "-") + "-agent"
    ROUTE_REGISTRY[_tt] = RouteDefinition(
        task_type=_tt,
        route_key=f"route:{_tt}",
        agent_type=_agent_type,
        allowed_tools=_TOOL_ALLOWLISTS[_tt],
        output_schema=_OUTPUT_SCHEMAS[_tt],
        max_steps=5,
        budget=_BUDGETS[_tt],
        is_default_lane=_tt in DEFAULT_LANES,
        description=_DESCRIPTIONS.get(_tt, ""),
    )


# --- Accessors ---

def get_route(task_type: str) -> RouteDefinition | None:
    """Get route definition by task type."""
    return ROUTE_REGISTRY.get(task_type)


def get_all_routes() -> list[RouteDefinition]:
    """Get all route definitions."""
    return list(ROUTE_REGISTRY.values())


def get_default_lane_routes() -> list[RouteDefinition]:
    """Get routes for default lanes only."""
    return [r for r in ROUTE_REGISTRY.values() if r.is_default_lane]


def get_specialist_routes() -> list[RouteDefinition]:
    """Get routes for specialist (non-default) lanes."""
    return [r for r in ROUTE_REGISTRY.values() if not r.is_default_lane]


def get_allowed_tools(task_type: str) -> tuple[str, ...]:
    """Get allowed tools for a task type."""
    route = ROUTE_REGISTRY.get(task_type)
    return route.allowed_tools if route else ()


def get_agent_type(task_type: str) -> str:
    """Get agent type for a task type."""
    route = ROUTE_REGISTRY.get(task_type)
    return route.agent_type if route else f"{task_type.replace('_', '-')}-agent"


def get_disallowed_tools() -> list[str]:
    """Get the list of tools disallowed for all subagents."""
    return list(_SUBAGENT_DISALLOWED_TOOLS)


def route_to_dict(route: RouteDefinition) -> dict[str, Any]:
    """Convert a route definition to a serializable dict."""
    return {
        "task_type": route.task_type,
        "route_key": route.route_key,
        "agent_type": route.agent_type,
        "allowed_tools": list(route.allowed_tools),
        "output_schema": route.output_schema,
        "max_steps": route.max_steps,
        "budget": route.budget,
        "is_default_lane": route.is_default_lane,
        "description": route.description,
    }
