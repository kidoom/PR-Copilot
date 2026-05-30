from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from backend.agent.model.messages import Message, ModelResponse
from backend.agent.tools.protocol import ToolSchema


class ModelClient(ABC):
    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        tool_schemas: list[ToolSchema] | None = None,
    ) -> ModelResponse: ...
