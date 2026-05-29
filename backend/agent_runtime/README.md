# Agent Framework

A minimal kgent-like agent runtime for controlled tool use, routable SubAgents, and observable reasoning steps.

## Architecture

```
backend/agent_runtime/
    __init__.py              # Re-exports all public API (backward compatible)
    README.md
    model/                   # Model I/O layer
        __init__.py
        messages.py          # Message, Role, ToolUseBlock, ToolResultBlock, ModelResponse, TokenUsage
        client.py            # ModelClient ABC (chat protocol)
        config.py            # ModelConfig (env-based configuration)
        openai_client.py     # OpenAIModelClient (OpenAI-compatible API client)
    tool/                    # Tool abstraction layer
        __init__.py
        protocol.py          # Tool ABC, RiskLevel, ToolSchema, project_schema
        registry.py          # ToolRegistry, filter_tools, DENIED_CHILD_TOOL_NAMES
    runtime/                 # Runtime orchestration layer
        __init__.py
        trace.py             # StepKind, ThinkStep, CallStep, ObserveStep, FinalStep, AgentStep
        results.py           # AgentResult, ToolExecutionResult
        agent_def.py         # AgentDefinition, AgentRegistry, UnknownAgentError
        loop.py              # run_loop (ReAct think-call-observe loop)
        sub_agent.py         # SubAgentResult
        task_tool.py         # TaskTool for SubAgent delegation
```

## Core Concepts

### Tool Protocol

Every tool implements the `Tool` abstract base class:

```python
from backend.agent_runtime import Tool, RiskLevel

class MyTool(Tool):
    @property
    def name(self) -> str: return "my_tool"

    @property
    def description(self) -> str: return "Does something"

    @property
    def input_schema(self) -> dict: return {"type": "object", "properties": {...}}

    @property
    def risk_level(self) -> RiskLevel: return RiskLevel.LOW

    @property
    def is_read_only(self) -> bool: return True

    @property
    def is_concurrency_safe(self) -> bool: return True

    async def call(self, input: dict) -> str:
        return "result"
```

Tools are registered in a `ToolRegistry` and resolved by name at runtime. The `project_schema` function strips runtime-only metadata (risk level) for model consumption.

### Agent Definitions

Agent definitions declare reusable agent types with prompts, step limits, and tool access:

```python
from backend.agent_runtime import AgentDefinition, AgentRegistry

registry = AgentRegistry()
registry.register(AgentDefinition(
    name="file_reviewer",
    description="Reviews file changes",
    system_prompt="You are a code reviewer. Analyze the provided diff.",
    default_max_steps=10,
    allowed_tools=["search", "read_file"],
    disallowed_tools=["write_file"],
))
```

The `task` tool is always removed from child agents to prevent recursive SubAgent spawning.

### ReAct Loop

The `run_loop` function implements a minimal think-call-observe cycle:

```python
from backend.agent_runtime import run_loop, Message, Role

result = await run_loop(
    model=model_client,
    tool_registry=tool_registry,
    messages=[Message(role=Role.USER, content="Review this PR")],
    max_steps=10,
)
```

The loop:
1. Calls the model with the current message history
2. If the model returns no tool calls, records a `FinalStep` and returns
3. If the model returns tool calls, executes each registered tool and appends observations
4. Stops at max steps with an explicit error

### TaskTool and SubAgent Delegation

The `TaskTool` delegates bounded work to child agents through an injected runner:

```python
from backend.agent_runtime import TaskTool, AgentRegistry

async def my_runner(agent_def, prompt, max_steps):
    # Run the agent with its definition, prompt, and step limit
    return SubAgentResult(output="...", agent_type=agent_def.name)

task_tool = TaskTool(agent_registry=registry, runner=my_runner)
result = await task_tool.run(
    prompt="Analyze security implications",
    agent_type="file_reviewer",
    max_steps=5,
)
```

## Mapping Context Task Planner to TaskTool

The Context Task Planner produces `ContextTask` items. `TaskTool.run()` accepts these fields:

| ContextTask field | TaskTool behavior |
|---|---|
| `task.intent` | Used directly as prompt |
| `task.queries` | Joined into multi-line prompt |
| `task.task_type` + `task.target_files` | Generates prompt: "Perform {task_type} on {files}" |
| `task.task_type` (no target) | Generates prompt: "Perform {task_type}" |
| `task.budget` | Maps to `max_steps` |

Example planner integration:

```python
for task in planner_output.tasks:
    agent_type = TASK_TYPE_TO_AGENT_MAP[task.task_type]
    result = await task_tool.run(
        task=task.to_dict(),
        agent_type=agent_type,
    )
```

## One Runner, Multiple Agent Types

All child agents share a single runner function. Agent differences come from `AgentDefinition`:
- Different system prompts
- Different default max steps
- Different allowed/disallowed tool sets

This means the seven context task categories (security, style, performance, etc.) all use the same runner with different agent definitions.

## Tree-Shaped Execution

The framework follows a tree-shaped execution model:

```
Main Agent (parent)
├── SubAgent A (child) ── isolated context, returns bounded result
├── SubAgent B (child) ── isolated context, returns bounded result
└── SubAgent C (child) ── isolated context, returns bounded result
```

Rules:
- A parent can delegate to child agents via TaskTool
- Each child receives only its delegated prompt and configured system context, not the full parent transcript
- Child agents cannot communicate with siblings directly
- Child results are returned as bounded `SubAgentResult` payloads to the parent
- `task`, `task_tool`, and `sub_agent` tools are universally denied for child agents, preventing recursive spawning

## Framework Independence

The agent framework is generic and independent from PR Review business logic:

- No imports of `PRContext`, `Evidence`, `GitHub` client, or review pipeline modules
- All unit tests run with fake model clients and fake tools
- No real model provider, repository writes, CI/CD commands, or shell commands are required
- The framework can be used for any agent-based task orchestration, not just PR review

## Staged Implementation History

This framework was implemented in five stages:

1. **Runtime Primitives**: Message models, tool use/result blocks, agent trace steps, agent results
2. **Tool Protocol and Registries**: Tool ABC, schema projection, ToolRegistry, AgentDefinition, AgentRegistry, tool filtering
3. **Minimal ReAct Loop**: ModelClient protocol, think-call-observe loop, unknown tool error handling
4. **TaskTool and SubAgent Shape**: TaskTool with validation, SubAgentResult, runner delegation
5. **Integration Documentation**: This document
