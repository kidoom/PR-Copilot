"""Parity tests ensuring planner routes and runtime SubAgent definitions cannot drift.

These tests verify that:
1. The shared route registry is the single source of truth
2. Planner task routes match the registry
3. Runtime agent tool allowlists match the registry
4. No task type is missing from either side
"""

from __future__ import annotations

from backend.domain.review.context_task_planner import (
    TASK_ROUTES,
    AGENT_DEFINITIONS,
    TASK_TYPES,
    DEFAULT_BUDGETS,
    ROUTE_KEYS,
    OUTPUT_SCHEMAS,
)
from backend.domain.review.route_registry import (
    ROUTE_REGISTRY,
    get_all_routes,
    get_allowed_tools,
    get_agent_type,
    get_disallowed_tools,
)
from backend.agent.subagents import (
    _AGENT_ALLOWED_TOOLS,
    _AGENT_DESCRIPTIONS,
    _DISALLOWED_TOOLS,
    build_default_subagent_registry,
)


class TestRouteRegistryParity:
    """Verify the shared registry is the single source of truth."""

    def test_all_task_types_have_route_definition(self):
        for tt in TASK_TYPES:
            assert tt in ROUTE_REGISTRY, f"Task type {tt} missing from ROUTE_REGISTRY"

    def test_route_registry_keys_match_planner_route_keys(self):
        for tt in TASK_TYPES:
            expected = f"route:{tt}"
            assert ROUTE_KEYS[tt] == expected
            assert ROUTE_REGISTRY[tt].route_key == expected

    def test_planner_budgets_match_registry(self):
        for tt in TASK_TYPES:
            assert DEFAULT_BUDGETS[tt] == ROUTE_REGISTRY[tt].budget

    def test_planner_output_schemas_match_registry(self):
        for tt in TASK_TYPES:
            assert OUTPUT_SCHEMAS[tt] == ROUTE_REGISTRY[tt].output_schema


class TestPlannerRouteParity:
    """Verify planner TASK_ROUTES match the shared registry."""

    def test_all_task_types_have_planner_route(self):
        for tt in TASK_TYPES:
            assert tt in TASK_ROUTES, f"Task type {tt} missing from TASK_ROUTES"

    def test_planner_route_agent_type_matches_registry(self):
        for tt in TASK_TYPES:
            expected = get_agent_type(tt)
            assert TASK_ROUTES[tt].agent_type == expected

    def test_planner_route_allowed_tools_match_registry(self):
        for tt in TASK_TYPES:
            expected = list(get_allowed_tools(tt))
            assert TASK_ROUTES[tt].allowed_tools == expected

    def test_planner_route_max_steps_match_registry(self):
        for tt in TASK_TYPES:
            assert TASK_ROUTES[tt].max_steps == ROUTE_REGISTRY[tt].max_steps


class TestRuntimeAgentParity:
    """Verify runtime agent definitions match the shared registry."""

    def test_all_agent_types_exist_in_subagents(self):
        registry = build_default_subagent_registry()
        registry_names = set(registry.names())
        expected_names = {get_agent_type(tt) for tt in TASK_TYPES}
        assert registry_names == expected_names

    def test_agent_allowed_tools_match_registry(self):
        for tt in TASK_TYPES:
            agent_type = get_agent_type(tt)
            expected = list(get_allowed_tools(tt))
            assert _AGENT_ALLOWED_TOOLS[agent_type] == expected

    def test_agent_descriptions_match_registry(self):
        for tt in TASK_TYPES:
            agent_type = get_agent_type(tt)
            expected = ROUTE_REGISTRY[tt].description
            assert _AGENT_DESCRIPTIONS[agent_type] == expected

    def test_disallowed_tools_match_registry(self):
        assert _DISALLOWED_TOOLS == get_disallowed_tools()

    def test_planner_agent_definitions_match_registry(self):
        for tt in TASK_TYPES:
            agent_type = get_agent_type(tt)
            assert agent_type in AGENT_DEFINITIONS
            assert AGENT_DEFINITIONS[agent_type].allowed_tools == list(get_allowed_tools(tt))
            assert AGENT_DEFINITIONS[agent_type].disallowed_tools == get_disallowed_tools()
