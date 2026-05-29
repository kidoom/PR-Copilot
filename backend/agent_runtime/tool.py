from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class ToolSchema:
    name: str
    description: str
    input_schema: dict[str, Any]


class Tool(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def input_schema(self) -> dict[str, Any]: ...

    @property
    @abstractmethod
    def risk_level(self) -> RiskLevel: ...

    @property
    @abstractmethod
    def is_read_only(self) -> bool: ...

    @property
    @abstractmethod
    def is_concurrency_safe(self) -> bool: ...

    @abstractmethod
    async def call(self, input: dict[str, Any]) -> str: ...


def project_schema(tool: Tool) -> ToolSchema:
    return ToolSchema(
        name=tool.name,
        description=tool.description,
        input_schema=tool.input_schema,
    )
