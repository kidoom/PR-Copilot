from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from backend.agent.model.client import ModelClient
from backend.agent.model.config import ModelConfig
from backend.agent.model.messages import Message, Role
from backend.agent.model.openai_client import OpenAIModelClient
from backend.agent.runtime.agent_def import AgentRegistry
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


MAIN_AGENT_SYSTEM_PROMPT = """\
You are the main PR review coordinator.

You receive a planner TaskPlan containing context tasks, routes, and agent
metadata. Do not perform code review directly. Your job is to call the `task`
tool with the full TaskPlan so the specialized read-only subagents can gather
evidence. After the task tool returns, summarize the subagent execution status
and do not invent evidence that was not returned by tools.
"""


@dataclass
class MainAgentRuntime:
    """Per-run wiring for one main agent execution."""

    task_tool: TaskTool
    tool_registry: ToolRegistry
    child_bundles: dict[str, ChildToolBundle] = field(default_factory=dict)


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
    main_agent_system_prompt: str = MAIN_AGENT_SYSTEM_PROMPT

    def new_model(self) -> ModelClient:
        return OpenAIModelClient(config=self.model_config)

    def build_main_messages(self, task_plan: dict[str, Any]) -> list[Message]:
        return [
            Message(role=Role.SYSTEM, content=self.main_agent_system_prompt),
            Message(
                role=Role.USER,
                content=json.dumps({
                    "instruction": "Call the task tool with this full task_plan.",
                    "task_plan": task_plan,
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
    ) -> MainAgentRuntime:
        child_bundles: dict[str, ChildToolBundle] = {}
        context_id = task_plan.get("context_id", "")

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
        )
        task_tool = TaskTool(
            runner=runner,
            agent_registry=self.subagent_registry,
        )
        tool_registry = ToolRegistry()
        tool_registry.register(task_tool)

        return MainAgentRuntime(
            task_tool=task_tool,
            tool_registry=tool_registry,
            child_bundles=child_bundles,
        )


_AGENT_DEPS: AgentDeps | None = None


def create_agent_deps(*, model_prefix: str = "OPENAI") -> AgentDeps:
    return AgentDeps(
        model_config=ModelConfig.from_env(prefix=model_prefix),
        subagent_registry=build_default_subagent_registry(),
        memory_store=FileMemoryStore(get_storage_dir()),
        compression_config=CompressionConfig.default(),
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
