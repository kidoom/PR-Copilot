from __future__ import annotations

from abc import ABC, abstractmethod

from .models import Message, ModelResponse


class ModelClient(ABC):
    @abstractmethod
    async def chat(self, messages: list[Message]) -> ModelResponse: ...
