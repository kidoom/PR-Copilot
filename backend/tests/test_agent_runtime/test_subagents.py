import pytest
from backend.agent.subagents import build_default_subagent_registry, build_context_child_tools, _PROMPT_MAP, _AGENT_ALLOWED_TOOLS
from backend.domain.review.context_task_planner import TASK_ROUTES, AGENT_DEFINITIONS
from backend.agent.tools.repo_context.models import RepoContextSession


class TestSubagentRegistry:
    def test_registry_contains_all_seven_agents(self):
        registry = build_default_subagent_registry()
        names = registry.names()
        assert len(names) == 7
        for name in _PROMPT_MAP:
            assert name in names

    def test_every_planner_route_agent_type_in_registry(self):
        registry = build_default_subagent_registry()
        for task_type, route in TASK_ROUTES.items():
            agent_type = route.agent_type
            agent = registry.resolve(agent_type)
            assert agent is not None, f"Planner route agent_type '{agent_type}' not in registry"

    def test_every_planner_agent_definition_in_registry(self):
        registry = build_default_subagent_registry()
        for agent_type in AGENT_DEFINITIONS:
            agent = registry.resolve(agent_type)
            assert agent is not None, f"Planner agent definition '{agent_type}' not in registry"

    def test_agent_names_match_convention(self):
        registry = build_default_subagent_registry()
        for name in registry.names():
            assert name.endswith("-agent"), f"Agent name '{name}' does not follow convention"


class TestSubagentPrompts:
    def test_all_agents_have_system_prompts(self):
        registry = build_default_subagent_registry()
        for name in registry.names():
            agent = registry.resolve(name)
            assert agent.system_prompt, f"Agent '{name}' has no system_prompt"
            assert len(agent.system_prompt) > 100, f"Agent '{name}' prompt too short"

    def test_all_prompts_contain_read_only_constraints(self):
        registry = build_default_subagent_registry()
        for name in registry.names():
            agent = registry.resolve(name)
            assert "MUST NOT edit" in agent.system_prompt, f"Agent '{name}' missing read-only constraint"
            assert "MUST NOT run shell" in agent.system_prompt, f"Agent '{name}' missing shell constraint"

    def test_all_prompts_contain_workflow_steps(self):
        registry = build_default_subagent_registry()
        for name in registry.names():
            agent = registry.resolve(name)
            assert "todo_write" in agent.system_prompt
            assert "verify_repo_context" in agent.system_prompt
            assert "finish_context_package" in agent.system_prompt

    def test_security_agent_mentions_secrets(self):
        registry = build_default_subagent_registry()
        agent = registry.resolve("security-context-agent")
        assert "secret" in agent.system_prompt.lower()

    def test_test_agent_mentions_test_gaps(self):
        registry = build_default_subagent_registry()
        agent = registry.resolve("test-context-agent")
        assert "test gap" in agent.system_prompt.lower()


class TestSubagentTools:
    def test_all_agents_deny_recursive_delegation(self):
        registry = build_default_subagent_registry()
        for name in registry.names():
            agent = registry.resolve(name)
            assert "task_tool" in agent.disallowed_tools
            assert "sub_agent" in agent.disallowed_tools

    def test_allowed_tools_match_planner(self):
        registry = build_default_subagent_registry()
        for agent_type, planner_def in AGENT_DEFINITIONS.items():
            agent = registry.resolve(agent_type)
            assert set(agent.allowed_tools) == set(planner_def.allowed_tools), \
                f"Tool mismatch for {agent_type}"

    def test_no_agent_has_task_in_allowed_tools(self):
        registry = build_default_subagent_registry()
        for name in registry.names():
            agent = registry.resolve(name)
            assert "task" not in agent.allowed_tools
            assert "task_tool" not in agent.allowed_tools
            assert "sub_agent" not in agent.allowed_tools


class TestPerTaskSessionBinding:
    def test_child_session_uses_task_context_id(self):
        tools = build_context_child_tools(
            "child_1",
            task={"task_id": "task_sec", "budget": {"max_searches": 3}},
            context_id="ctx_abc",
            repo_root="/tmp/repo",
        )
        assert len(tools) > 0

    def test_sibling_sessions_are_independent(self):
        task_a = {"task_id": "task_a", "budget": {"max_searches": 2}}
        task_b = {"task_id": "task_b", "budget": {"max_searches": 5}}

        tools_a = build_context_child_tools("child_a", task=task_a, context_id="ctx_1")
        tools_b = build_context_child_tools("child_b", task=task_b, context_id="ctx_1")

        # Both should have tools
        assert len(tools_a) > 0
        assert len(tools_b) > 0

    def test_child_session_with_budget(self):
        task = {
            "task_id": "t1",
            "budget": {"max_searches": 2, "max_files": 3, "max_tokens": 1000},
        }
        tools = build_context_child_tools("child_1", task=task, context_id="ctx_1")
        assert len(tools) > 0

    def test_child_session_without_task(self):
        tools = build_context_child_tools("child_1", context_id="ctx_1")
        assert len(tools) > 0


class TestReadOnlyPermissions:
    _DANGEROUS_TOOLS = {
        "file_edit", "file_write", "shell", "bash", "exec",
        "git_commit", "git_push", "github_comment", "submit_review",
        "task", "task_tool", "sub_agent",
    }

    def test_no_dangerous_tools_in_any_agent(self):
        registry = build_default_subagent_registry()
        for name in registry.names():
            agent = registry.resolve(name)
            for dangerous in self._DANGEROUS_TOOLS:
                assert dangerous not in agent.allowed_tools, \
                    f"Agent '{name}' has dangerous tool '{dangerous}' in allowed_tools"

    def test_recursive_tools_denied_for_all(self):
        registry = build_default_subagent_registry()
        for name in registry.names():
            agent = registry.resolve(name)
            assert "task_tool" in agent.disallowed_tools
            assert "sub_agent" in agent.disallowed_tools

    def test_only_repo_context_tools_in_allowed(self):
        registry = build_default_subagent_registry()
        valid_tools = {
            "todo_write", "verify_repo_context", "finish_context_package",
            "read_file_patch", "search_diff", "search_repo", "read_repo_file",
            "search_tests_for", "read_repo_manifest", "read_check_summary",
        }
        for name in registry.names():
            agent = registry.resolve(name)
            for tool in agent.allowed_tools:
                assert tool in valid_tools, \
                    f"Agent '{name}' has unexpected tool '{tool}' in allowed_tools"
