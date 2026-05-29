from __future__ import annotations

from abc import ABC, abstractmethod

from backend.agent_runtime.model.messages import Message, ModelResponse


class ModelClient(ABC):
    @abstractmethod
    async def chat(self, messages: list[Message]) -> ModelResponse: ...
