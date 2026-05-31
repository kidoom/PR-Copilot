from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from backend.agent.model.messages import Message, ModelResponse, TextDeltaCallback
from backend.agent.tools.protocol import ToolSchema


class ModelClient(ABC):
    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        tool_schemas: list[ToolSchema] | None = None,
    ) -> ModelResponse: ...

    async def chat_stream(
        self,
        messages: list[Message],
        tool_schemas: list[ToolSchema] | None = None,
        on_text_delta: TextDeltaCallback | None = None,
    ) -> ModelResponse:
        """Streaming-compatible entry point. Default falls back to chat().

        Subclasses may override to provide real streaming. The on_text_delta
        callback receives only visible assistant content text, not tool
        arguments or reasoning fields.
        """
        return await self.chat(messages, tool_schemas=tool_schemas)
