from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from backend.agent.model.client import ModelClient
from backend.agent.model.config import ModelConfig
from backend.agent.model.messages import Message, Role
from backend.agent.model.openai_client import OpenAIModelClient
from backend.agent.runtime.agent_def import AgentRegistry
from backend.agent.runtime.cancellation import CancellationProbe
from backend.agent.runtime.compression.config import CompressionConfig
from backend.agent.runtime.memory.config import get_storage_dir
from backend.agent.runtime.memory.store import FileMemoryStore
from backend.agent.runtime.subagent_runner import build_subagent_runner
from backend.agent.subagents import (
    ChildToolBundle,
    build_context_child_tools,
    build_default_subagent_registry,
)
from backend.agent.tools.registry import ToolRegistry
from backend.agent.tools.task import TaskTool
from backend.domain.github.checks_provider import ChecksSummaryProvider
from backend.domain.github.client import GitHubClient

logger = logging.getLogger(__name__)


def _default_repo_temp_root() -> str:
    configured = os.environ.get("PR_COPILOT_REPO_TEMP_ROOT")
    if configured:
        return configured
    return str(Path(get_storage_dir()) / "repo-workspaces")


def _default_max_concurrent_tasks() -> int:
    try:
        return max(1, int(os.environ.get("PR_COPILOT_MAX_CONCURRENT_TASKS", "6")))
    except ValueError:
        return 6


def create_workspace_manager() -> Any:
    from backend.agent.tools.repo_context.provider.workspace import RepoWorkspaceManager

    return RepoWorkspaceManager(temp_root=_default_repo_temp_root())


class WorkspacePreparationError(Exception):
    """Raised when workspace preparation fails before subagent dispatch."""

    def __init__(self, error: Any) -> None:
        self.preparation_error = error
        super().__init__(f"Workspace preparation failed: {getattr(error, 'message', error)}")


MAIN_AGENT_SYSTEM_PROMPT = """\
You are the main PR review coordinator.

The server has already dispatched the planner TaskPlan to specialized read-only
subagents. You receive their validated results. Synthesize a concise final PR
review result and do not invent evidence that was not returned by subagents.

When you produce visible assistant text (not tool calls), write concise
user-safe progress updates or final synthesis. Do not include internal
reasoning, investigation steps, or raw tool output in your visible text.
"""


@dataclass
class MainAgentRuntime:
    """Per-run wiring for one main agent execution."""

    task_tool: TaskTool
    tool_registry: ToolRegistry
    child_bundles: dict[str, ChildToolBundle] = field(default_factory=dict)
    workspace_manager: Any = None
    run_id: str = ""

    def cleanup_workspace(self) -> None:
        if self.workspace_manager and self.run_id:
            try:
                self.workspace_manager.cleanup_run(self.run_id)
            except Exception:
                logger.warning("Failed to cleanup workspace for run %s", self.run_id, exc_info=True)


@dataclass
class AgentDeps:
    """Application-wide agent dependencies preloaded at startup.

    Static pieces live here: model config, subagent definitions, and prompt
    constants. Request-specific state such as TaskPlan, PRContext, repo root,
    child sessions, and tool bundles is created by the builder methods.
    """

    model_config: ModelConfig
    subagent_registry: AgentRegistry
    memory_store: FileMemoryStore = field(default_factory=lambda: FileMemoryStore(get_storage_dir()))
    compression_config: CompressionConfig = field(default_factory=CompressionConfig.default)
    workspace_manager: Any = field(default_factory=create_workspace_manager)
    main_agent_system_prompt: str = MAIN_AGENT_SYSTEM_PROMPT
    max_concurrent_tasks: int = field(default_factory=_default_max_concurrent_tasks)

    def new_model(self) -> ModelClient:
        return OpenAIModelClient(config=self.model_config)

    def build_main_messages(
        self,
        task_plan: dict[str, Any],
        task_results: list[dict[str, Any]] | None = None,
    ) -> list[Message]:
        task_type_counts: dict[str, int] = {}
        for task in task_plan.get("tasks", []):
            task_type = task.get("task_type", "") if isinstance(task, dict) else ""
            if task_type:
                task_type_counts[task_type] = task_type_counts.get(task_type, 0) + 1

        synthesis_results = []
        for result in task_results or []:
            synthesis_results.append({
                "task_id": result.get("task_id", ""),
                "task_type": result.get("task_type", ""),
                "agent_type": result.get("agent_type", ""),
                "status": result.get("status", ""),
                "parse_status": result.get("parse_status", ""),
                "validation_errors": result.get("validation_errors", []),
                "parsed_result": result.get("parsed_result"),
            })

        return [
            Message(role=Role.SYSTEM, content=self.main_agent_system_prompt),
            Message(
                role=Role.USER,
                content=json.dumps({
                    "instruction": (
                        "Synthesize the completed specialized PR-review task results. "
                        "Use only evidence-backed findings present in parsed_result."
                    ),
                    "task_plan_summary": {
                        "context_id": task_plan.get("context_id", ""),
                        "task_count": len(task_plan.get("tasks", [])),
                        "task_type_counts": task_type_counts,
                    },
                    "task_results": synthesis_results,
                }, ensure_ascii=False),
            ),
        ]

    def build_main_runtime(
        self,
        *,
        model: ModelClient,
        task_plan: dict[str, Any],
        pr_context: Any,
        repo_root: str,
        parent_session_id: str | None = None,
        run_id: str = "",
        workspace_manager: Any = None,
        pr_identity: Any = None,
        token: str | None = None,
        on_runtime_event: Callable[[str, dict[str, Any]], None] | None = None,
        cancellation_probe: CancellationProbe | None = None,
    ) -> MainAgentRuntime:
        child_bundles: dict[str, ChildToolBundle] = {}
        context_id = task_plan.get("context_id", "")

        active_workspace_manager = workspace_manager or self.workspace_manager
        provider = None
        checks_provider = None
        if active_workspace_manager is not None and pr_identity is not None:

            result = active_workspace_manager.prepare_workspace(
                run_id=run_id,
                context_id=context_id,
                pr_identity=pr_identity,
                local_repo_root=repo_root if repo_root else None,
                token=token,
            )
            if result.error is not None:
                raise WorkspacePreparationError(result.error)
            provider = active_workspace_manager.get_provider(run_id, context_id)

            # Create GitHub Checks provider when credentials are available
            if token and hasattr(pr_identity, 'owner') and hasattr(pr_identity, 'repo') and hasattr(pr_identity, 'head_sha'):
                gh_client = GitHubClient(token=token, cancellation_probe=cancellation_probe)
                checks_provider = ChecksSummaryProvider(
                    owner=pr_identity.owner,
                    repo=pr_identity.repo,
                    head_sha=pr_identity.head_sha,
                    github_client=gh_client,
                    cancellation_probe=cancellation_probe,
                )

        def child_tool_factory(
            child_session_id: str,
            task: dict[str, Any] | None = None,
        ) -> ChildToolBundle:
            task_context_id = ""
            if task is not None:
                task_context_id = task.get("context_id", "")
            bundle = build_context_child_tools(
                child_session_id,
                task=task,
                context_id=task_context_id or context_id,
                repo_root=repo_root,
                pr_context=pr_context,
                provider=provider,
                pr_identity=pr_identity,
                cancellation_probe=cancellation_probe,
                checks_provider=checks_provider,
            )
            child_bundles[child_session_id] = bundle
            return bundle

        runner = build_subagent_runner(
            model=model,
            parent_session_id=parent_session_id or context_id or "main",
            agent_registry=self.subagent_registry,
            child_tool_factory=child_tool_factory,
            memory_store=self.memory_store,
            run_id=run_id,
            context_id=context_id,
            compression_config=self.compression_config,
            on_runtime_event=on_runtime_event,
            cancellation_probe=cancellation_probe,
        )
        task_tool = TaskTool(
            runner=runner,
            agent_registry=self.subagent_registry,
            max_concurrent_tasks=self.max_concurrent_tasks,
            cancellation_probe=cancellation_probe,
        )
        tool_registry = ToolRegistry()
        tool_registry.register(task_tool)

        return MainAgentRuntime(
            task_tool=task_tool,
            tool_registry=tool_registry,
            child_bundles=child_bundles,
            workspace_manager=active_workspace_manager if provider is not None else None,
            run_id=run_id,
        )


_AGENT_DEPS: AgentDeps | None = None


def create_agent_deps(*, model_prefix: str = "OPENAI") -> AgentDeps:
    return AgentDeps(
        model_config=ModelConfig.from_env(prefix=model_prefix),
        subagent_registry=build_default_subagent_registry(),
        memory_store=FileMemoryStore(get_storage_dir()),
        compression_config=CompressionConfig.default(),
        workspace_manager=create_workspace_manager(),
    )


def preload_agent_deps(*, model_prefix: str = "OPENAI") -> AgentDeps:
    global _AGENT_DEPS
    _AGENT_DEPS = create_agent_deps(model_prefix=model_prefix)
    return _AGENT_DEPS


def get_agent_deps() -> AgentDeps:
    global _AGENT_DEPS
    if _AGENT_DEPS is None:
        _AGENT_DEPS = create_agent_deps()
    return _AGENT_DEPS


def set_agent_deps(deps: AgentDeps | None) -> None:
    global _AGENT_DEPS
    _AGENT_DEPS = deps
