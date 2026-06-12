from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

import httpx

from backend.agent.model.config import ModelConfig
from backend.agent.model.client import ModelClient
from backend.agent.model.messages import (
    MAX_VISIBLE_DELTA_CHARS,
    Message,
    ModelResponse,
    Role,
    TextDeltaCallback,
    TokenUsage,
    ToolResultBlock,
    ToolUseBlock,
    truncate_delta,
)
from backend.agent.tools.protocol import ToolSchema

RETRYABLE_HTTP_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def _retry_delay(response: httpx.Response, attempt: int, base_delay: float) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return max(0.0, min(float(retry_after), 30.0))
        except ValueError:
            pass
    return min(base_delay * (2 ** attempt), 10.0)


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
        max_retries: int = 2,
        retry_base_delay: float = 1.0,
    ) -> None:
        self._config = config
        self._tool_schemas = tool_schemas or []
        self._client = http_client or httpx.AsyncClient(timeout=120.0)
        self._owns_client = http_client is None
        self._max_retries = max(0, int(max_retries))
        self._retry_base_delay = max(0.0, float(retry_base_delay))

    async def _post_json_with_retries(
        self,
        url: str,
        *,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            resp = await self._client.post(url, json=payload, headers=headers)
            if (
                resp.status_code in RETRYABLE_HTTP_STATUS_CODES
                and attempt < self._max_retries
            ):
                await asyncio.sleep(_retry_delay(resp, attempt, self._retry_base_delay))
                continue
            resp.raise_for_status()
            return resp
        raise RuntimeError("Model request retry loop exited unexpectedly")

    async def chat(
        self,
        messages: list[Message],
        tool_schemas: list[ToolSchema] | None = None,
    ) -> ModelResponse:
        openai_messages = [_convert_message(m) for m in messages]

        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": openai_messages,
            "temperature": self._config.temperature,
            "max_tokens": self._config.max_output_tokens,
        }

        effective_schemas = tool_schemas if tool_schemas is not None else self._tool_schemas
        if effective_schemas:
            payload["tools"] = build_tools_param(effective_schemas)

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._config.api_key}",
        }

        resp = await self._post_json_with_retries(
            f"{self._config.base_url}/chat/completions",
            payload=payload,
            headers=headers,
        )

        return _parse_response(resp.json())

    async def chat_stream(
        self,
        messages: list[Message],
        tool_schemas: list[ToolSchema] | None = None,
        on_text_delta: TextDeltaCallback | None = None,
    ) -> ModelResponse:
        """OpenAI-compatible streamed chat completions with SSE chunk parsing.

        Accumulates visible content, incremental tool-call ids, names,
        arguments, and token usage into the existing final ModelResponse shape.
        Excludes reasoning fields from visible text delta callbacks.
        """
        openai_messages = [_convert_message(m) for m in messages]

        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": openai_messages,
            "temperature": self._config.temperature,
            "max_tokens": self._config.max_output_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        effective_schemas = tool_schemas if tool_schemas is not None else self._tool_schemas
        if effective_schemas:
            payload["tools"] = build_tools_param(effective_schemas)

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._config.api_key}",
        }

        # Accumulators for streamed data
        content_parts: list[str] = []
        # tool_call index -> {id, name, arguments_parts}
        tool_calls_acc: dict[int, dict[str, Any]] = {}
        input_tokens = 0
        output_tokens = 0

        async with self._client.stream(
            "POST",
            f"{self._config.base_url}/chat/completions",
            json=payload,
            headers=headers,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                # SSE format: "data: {...}" or "data: [DONE]"
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    # Parse usage if present
                    usage = chunk.get("usage")
                    if usage:
                        input_tokens = usage.get("prompt_tokens", input_tokens)
                        output_tokens = usage.get("completion_tokens", output_tokens)

                    choices = chunk.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    finish_reason = choices[0].get("finish_reason")

                    # Accumulate visible content (exclude reasoning fields)
                    # OpenAI-style streaming uses "content" for visible text.
                    # Reasoning fields like "reasoning_content" or "thinking" are
                    # provider-specific and must NOT be emitted as visible deltas.
                    visible_content = delta.get("content")
                    if visible_content:
                        content_parts.append(visible_content)
                        if on_text_delta:
                            bounded = truncate_delta(visible_content, MAX_VISIBLE_DELTA_CHARS)
                            if bounded:
                                on_text_delta(bounded)

                    # Accumulate tool calls
                    for tc_delta in delta.get("tool_calls", []) or []:
                        idx = tc_delta.get("index", 0)
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {
                                "id": tc_delta.get("id", ""),
                                "name": "",
                                "arguments_parts": [],
                            }
                        acc = tool_calls_acc[idx]
                        if tc_delta.get("id"):
                            acc["id"] = tc_delta["id"]
                        func = tc_delta.get("function", {})
                        if func.get("name"):
                            acc["name"] = func["name"]
                        if func.get("arguments"):
                            acc["arguments_parts"].append(func["arguments"])

        # Build final ModelResponse from accumulated data
        full_content = "".join(content_parts)

        tool_use_blocks: list[ToolUseBlock] = []
        for idx in sorted(tool_calls_acc.keys()):
            acc = tool_calls_acc[idx]
            args_str = "".join(acc["arguments_parts"])
            try:
                args = json.loads(args_str)
            except (json.JSONDecodeError, TypeError):
                args = {}
            tool_use_blocks.append(ToolUseBlock(
                tool_use_id=acc["id"],
                name=acc["name"],
                input=args,
            ))

        token_usage = TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        return ModelResponse(
            content=full_content,
            tool_use_blocks=tool_use_blocks,
            token_usage=token_usage,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> OpenAIModelClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
