from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


@dataclass(frozen=True)
class ToolUseBlock:
    tool_use_id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class Message:
    role: Role
    content: str | list[ToolUseBlock | ToolResultBlock] = ""
    token_usage: TokenUsage | None = None


@dataclass
class ModelResponse:
    content: str
    tool_use_blocks: list[ToolUseBlock] = field(default_factory=list)
    token_usage: TokenUsage = field(default_factory=TokenUsage)
