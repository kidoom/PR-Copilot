from __future__ import annotations

from dataclasses import dataclass, field

from backend.agent.runtime.trace import AgentStep
from backend.agent.model.messages import TokenUsage


@dataclass
class ToolExecutionResult:
    tool_use_id: str
    output: str
    is_error: bool = False


@dataclass
class AgentResult:
    output: str
    steps: list[AgentStep] = field(default_factory=list)
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    stopped_by_max_steps: bool = False
