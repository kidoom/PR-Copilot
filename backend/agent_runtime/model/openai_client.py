from __future__ import annotations

import json
from typing import Any

import httpx

from backend.agent_runtime.model.config import ModelConfig
from backend.agent_runtime.model.client import ModelClient
from backend.agent_runtime.model.messages import (
    Message,
    ModelResponse,
    Role,
    TokenUsage,
    ToolResultBlock,
    ToolUseBlock,
)
from backend.agent_runtime.tool.protocol import ToolSchema


def build_tools_param(schemas: list[ToolSchema]) -> list[dict[str, Any]]:
    tools = []
    for s in schemas:
        tools.append({
            "type": "function",
            "function": {
                "name": s.name,
                "description": s.description,
                "parameters": s.input_schema,
            },
        })
    return tools


def _convert_message(msg: Message) -> dict[str, Any]:
    if msg.role == Role.SYSTEM:
        return {"role": "system", "content": msg.content}

    if msg.role == Role.USER:
        return {"role": "user", "content": msg.content}

    if msg.role == Role.ASSISTANT:
        if isinstance(msg.content, str):
            return {"role": "assistant", "content": msg.content}
        tool_calls = []
        text_parts = []
        for block in msg.content:
            if isinstance(block, ToolUseBlock):
                tool_calls.append({
                    "id": block.tool_use_id,
                    "type": "function",
                    "function": {
                        "name": block.name,
                        "arguments": json.dumps(block.input),
                    },
                })
            else:
                text_parts.append(str(block))
        result: dict[str, Any] = {"role": "assistant"}
        if text_parts:
            result["content"] = "\n".join(text_parts)
        if tool_calls:
            result["tool_calls"] = tool_calls
        return result

    if msg.role == Role.TOOL:
        if isinstance(msg.content, list):
            parts = []
            for block in msg.content:
                if isinstance(block, ToolResultBlock):
                    parts.append(block.content)
            content = "\n".join(parts) if parts else ""
        else:
            content = str(msg.content)
        tool_call_id = ""
        if isinstance(msg.content, list):
            for block in msg.content:
                if isinstance(block, ToolResultBlock):
                    tool_call_id = block.tool_use_id
                    break
        return {
            "role": "tool",
            "content": content,
            "tool_call_id": tool_call_id,
        }

    return {"role": "user", "content": str(msg.content)}


def _parse_response(data: dict[str, Any]) -> ModelResponse:
    choice = data["choices"][0]
    message = choice["message"]

    content = message.get("content") or ""
    tool_use_blocks: list[ToolUseBlock] = []

    for tc in message.get("tool_calls", []) or []:
        func = tc["function"]
        try:
            args = json.loads(func["arguments"])
        except (json.JSONDecodeError, TypeError):
            args = {}
        tool_use_blocks.append(ToolUseBlock(
            tool_use_id=tc["id"],
            name=func["name"],
            input=args,
        ))

    usage = data.get("usage", {})
    token_usage = TokenUsage(
        input_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
    )

    return ModelResponse(
        content=content,
        tool_use_blocks=tool_use_blocks,
        token_usage=token_usage,
    )


class OpenAIModelClient(ModelClient):
    def __init__(
        self,
        config: ModelConfig,
        *,
        tool_schemas: list[ToolSchema] | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._tool_schemas = tool_schemas or []
        self._client = http_client or httpx.AsyncClient(timeout=120.0)
        self._owns_client = http_client is None

    async def chat(self, messages: list[Message]) -> ModelResponse:
        openai_messages = [_convert_message(m) for m in messages]

        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": openai_messages,
            "temperature": self._config.temperature,
            "max_tokens": self._config.max_output_tokens,
        }

        if self._tool_schemas:
            payload["tools"] = build_tools_param(self._tool_schemas)

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._config.api_key}",
        }

        resp = await self._client.post(
            f"{self._config.base_url}/chat/completions",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()

        return _parse_response(resp.json())

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> OpenAIModelClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
