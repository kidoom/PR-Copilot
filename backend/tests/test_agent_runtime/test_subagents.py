import pytest
import tempfile
import os
from backend.agent.subagents import build_default_subagent_registry, build_context_child_tools, ChildToolBundle, _PROMPT_MAP, _AGENT_ALLOWED_TOOLS
from backend.domain.review.context_task_planner import TASK_ROUTES, AGENT_DEFINITIONS
from backend.agent.tools.repo_context.models import RepoContextSession


@pytest.fixture
def temp_repo():
    """Create a temporary repository for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create .git directory to make it look like a repo
        os.makedirs(os.path.join(tmpdir, ".git"))
        yield tmpdir


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
            # finish_context_package removed - subagents now output structured JSON directly
            assert "final review result" in agent.system_prompt.lower() or "json" in agent.system_prompt.lower()

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
            # Remove finish_context_package from planner tools for comparison
            expected_tools = set(planner_def.allowed_tools) - {"finish_context_package"}
            assert set(agent.allowed_tools) == expected_tools, \
                f"Tool mismatch for {agent_type}"

    def test_no_agent_has_task_in_allowed_tools(self):
        registry = build_default_subagent_registry()
        for name in registry.names():
            agent = registry.resolve(name)
            assert "task" not in agent.allowed_tools
            assert "task_tool" not in agent.allowed_tools
            assert "sub_agent" not in agent.allowed_tools


class TestPerTaskSessionBinding:
    def test_returns_bundle_with_session(self, temp_repo):
        bundle = build_context_child_tools(
            "child_1",
            task={"task_id": "task_sec", "budget": {"max_searches": 3}},
            context_id="ctx_abc",
            repo_root=temp_repo,
        )
        assert isinstance(bundle, ChildToolBundle)
        assert isinstance(bundle.session, RepoContextSession)
        assert len(bundle.tools) > 0

    def test_session_uses_provided_context_id(self, temp_repo):
        bundle = build_context_child_tools(
            "child_1",
            task={"task_id": "task_sec"},
            context_id="ctx_abc",
            repo_root=temp_repo,
        )
        assert bundle.session.context_id == "ctx_abc"

    def test_session_uses_task_id(self, temp_repo):
        bundle = build_context_child_tools(
            "child_1",
            task={"task_id": "task_sec"},
            context_id="ctx_abc",
            repo_root=temp_repo,
        )
        assert bundle.session.task_id == "task_sec"

    def test_session_falls_back_to_child_session_id(self, temp_repo):
        bundle = build_context_child_tools("child_xyz", repo_root=temp_repo)
        assert bundle.session.context_id == "child_xyz"
        assert bundle.session.task_id == "child_xyz"

    def test_session_budget_from_task(self, temp_repo):
        # Budget is no longer customizable per-task in stateless mode
        bundle = build_context_child_tools(
            "child_1",
            task={"task_id": "t1"},
            repo_root=temp_repo,
        )
        # Uses default budget
        assert bundle.session.budget.max_searches == 5
        assert bundle.session.budget.max_files == 10
        assert bundle.session.budget.max_tokens == 3000

    def test_session_default_budget(self, temp_repo):
        bundle = build_context_child_tools("child_1", repo_root=temp_repo)
        assert bundle.session.budget.max_searches == 5
        assert bundle.session.budget.max_files == 10
        assert bundle.session.budget.max_tokens == 3000

    def test_sibling_sessions_are_independent(self, temp_repo):
        task_a = {"task_id": "task_a"}
        task_b = {"task_id": "task_b"}

        bundle_a = build_context_child_tools("child_a", task=task_a, context_id="ctx_1", repo_root=temp_repo)
        bundle_b = build_context_child_tools("child_b", task=task_b, context_id="ctx_1", repo_root=temp_repo)

        assert bundle_a.session is not bundle_b.session
        assert bundle_a.session.task_id == "task_a"
        assert bundle_b.session.task_id == "task_b"
        # Both use default budget in stateless mode
        assert bundle_a.session.budget.max_searches == 5
        assert bundle_b.session.budget.max_searches == 5

    def test_sibling_todos_are_isolated(self, temp_repo):
        bundle_a = build_context_child_tools("child_a", task={"task_id": "t_a"}, context_id="ctx_1", repo_root=temp_repo)
        bundle_b = build_context_child_tools("child_b", task={"task_id": "t_b"}, context_id="ctx_1", repo_root=temp_repo)

        bundle_a.session.todos = [{"content": "step 1", "status": "in_progress"}]
        assert len(bundle_b.session.todos) == 0

    def test_sibling_final_package_is_isolated(self, temp_repo):
        bundle_a = build_context_child_tools("child_a", task={"task_id": "t_a"}, context_id="ctx_1", repo_root=temp_repo)
        bundle_b = build_context_child_tools("child_b", task={"task_id": "t_b"}, context_id="ctx_1", repo_root=temp_repo)

        bundle_a.session.final_package = "some_package"
        assert bundle_b.session.final_package is None


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
            "todo_write", "verify_repo_context",
            "read_file_patch", "search_diff", "search_repo", "read_repo_file",
            "search_tests_for", "read_repo_manifest", "read_check_summary",
        }
        for name in registry.names():
            agent = registry.resolve(name)
            for tool in agent.allowed_tools:
                assert tool in valid_tools, \
                    f"Agent '{name}' has unexpected tool '{tool}' in allowed_tools"
