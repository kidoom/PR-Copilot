from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StepKind(str, Enum):
    THINK = "think"
    CALL = "call"
    OBSERVE = "observe"
    FINAL = "final"


@dataclass
class ThinkStep:
    kind: StepKind = StepKind.THINK
    reasoning: str = ""


@dataclass
class CallStep:
    tool_name: str
    tool_input: dict[str, Any] = field(default_factory=dict)
    tool_use_id: str = ""
    kind: StepKind = StepKind.CALL


@dataclass
class ObserveStep:
    tool_use_id: str
    output: str
    is_error: bool = False
    kind: StepKind = StepKind.OBSERVE


@dataclass
class FinalStep:
    output: str
    kind: StepKind = StepKind.FINAL


AgentStep = ThinkStep | CallStep | ObserveStep | FinalStep
