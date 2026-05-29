from __future__ import annotations

from dataclasses import dataclass, field

from .trace import AgentStep
from .models import TokenUsage


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
