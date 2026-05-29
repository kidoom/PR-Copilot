from __future__ import annotations

import json
import pytest
from typing import Any

import httpx

from backend.agent_runtime.model.config import ModelConfig
from backend.agent_runtime.model.messages import Message, Role, ToolUseBlock, ToolResultBlock
from backend.agent_runtime.model.openai_client import OpenAIModelClient, build_tools_param, _convert_message, _parse_response
from backend.agent_runtime.tool.protocol import ToolSchema


def _make_config(**overrides) -> ModelConfig:
    defaults = dict(api_key="sk-test", base_url="https://api.openai.com/v1", model="gpt-4o", max_output_tokens=4096, temperature=0.0)
    defaults.update(overrides)
    return ModelConfig(**defaults)


def _mock_response(data: dict[str, Any], status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code=status_code, json=data, request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))


SIMPLE_RESPONSE = {
    "id": "chatcmpl-123", "object": "chat.completion",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hello!"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
}

TOOL_CALL_RESPONSE = {
    "id": "chatcmpl-456", "object": "chat.completion",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": None, "tool_calls": [{"id": "call_abc", "type": "function", "function": {"name": "search", "arguments": json.dumps({"query": "test"})}}]}, "finish_reason": "tool_calls"}],
    "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
}


class MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = list(responses)
        self._call_count = 0
        self.captured_requests: list[dict[str, Any]] = []

    async def handle_async_request(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        body = json.loads(await request.aread())
        self.captured_requests.append(body)
        resp = self._responses[self._call_count]
        self._call_count += 1
        return resp


def test_build_tools_param_empty():
    assert build_tools_param([]) == []


def test_build_tools_param_single():
    schema = ToolSchema(name="search", description="Search files", input_schema={"type": "object", "properties": {"q": {"type": "string"}}})
    result = build_tools_param([schema])
    assert len(result) == 1
    assert result[0]["function"]["name"] == "search"


def test_convert_system_message():
    assert _convert_message(Message(role=Role.SYSTEM, content="sys")) == {"role": "system", "content": "sys"}


def test_convert_user_message():
    assert _convert_message(Message(role=Role.USER, content="hi")) == {"role": "user", "content": "hi"}


def test_convert_assistant_string():
    assert _convert_message(Message(role=Role.ASSISTANT, content="ok")) == {"role": "assistant", "content": "ok"}


def test_convert_assistant_with_tool_use():
    block = ToolUseBlock(tool_use_id="u1", name="search", input={"q": "test"})
    result = _convert_message(Message(role=Role.ASSISTANT, content=[block]))
    assert result["role"] == "assistant"
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["id"] == "u1"


def test_convert_tool_message():
    block = ToolResultBlock(tool_use_id="u1", content="result data")
    result = _convert_message(Message(role=Role.TOOL, content=[block]))
    assert result["role"] == "tool"
    assert result["content"] == "result data"
    assert result["tool_call_id"] == "u1"


def test_parse_simple_response():
    resp = _parse_response(SIMPLE_RESPONSE)
    assert resp.content == "Hello!"
    assert resp.tool_use_blocks == []
    assert resp.token_usage.input_tokens == 10


def test_parse_tool_call_response():
    resp = _parse_response(TOOL_CALL_RESPONSE)
    assert resp.content == ""
    assert len(resp.tool_use_blocks) == 1
    assert resp.tool_use_blocks[0].name == "search"
    assert resp.tool_use_blocks[0].input == {"query": "test"}


@pytest.mark.asyncio
async def test_simple_chat():
    transport = MockTransport([_mock_response(SIMPLE_RESPONSE)])
    client = httpx.AsyncClient(transport=transport)
    config = _make_config()
    model = OpenAIModelClient(config=config, http_client=client)
    result = await model.chat([Message(role=Role.USER, content="hi")])
    assert result.content == "Hello!"


@pytest.mark.asyncio
async def test_chat_sends_correct_payload():
    transport = MockTransport([_mock_response(SIMPLE_RESPONSE)])
    client = httpx.AsyncClient(transport=transport)
    config = _make_config(api_key="sk-secret", model="gpt-4o-mini", base_url="https://custom.api.com/v1")
    model = OpenAIModelClient(config=config, http_client=client)
    await model.chat([Message(role=Role.SYSTEM, content="sys"), Message(role=Role.USER, content="hi")])
    body = transport.captured_requests[0]
    assert body["model"] == "gpt-4o-mini"
    assert body["temperature"] == 0.0


@pytest.mark.asyncio
async def test_chat_with_tools():
    transport = MockTransport([_mock_response(TOOL_CALL_RESPONSE)])
    client = httpx.AsyncClient(transport=transport)
    config = _make_config()
    schemas = [ToolSchema(name="search", description="Search", input_schema={"type": "object"})]
    model = OpenAIModelClient(config=config, http_client=client, tool_schemas=schemas)
    result = await model.chat([Message(role=Role.USER, content="search for X")])
    body = transport.captured_requests[0]
    assert "tools" in body
    assert len(result.tool_use_blocks) == 1


@pytest.mark.asyncio
async def test_http_error_raises():
    error_resp = httpx.Response(status_code=401, json={"error": {"message": "Invalid API key"}}, request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))
    transport = MockTransport([error_resp])
    client = httpx.AsyncClient(transport=transport)
    config = _make_config(api_key="bad-key")
    model = OpenAIModelClient(config=config, http_client=client)
    with pytest.raises(httpx.HTTPStatusError):
        await model.chat([Message(role=Role.USER, content="hi")])


def test_config_defaults():
    config = ModelConfig(api_key="sk-test")
    assert config.base_url == "https://api.openai.com/v1"
    assert config.model == "gpt-4o"


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-3.5-turbo")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.api.com/v1")
    config = ModelConfig.from_env()
    assert config.api_key == "sk-env-key"
    assert config.model == "gpt-3.5-turbo"


def test_config_from_env_custom_prefix(monkeypatch):
    monkeypatch.setenv("CUSTOM_API_KEY", "sk-custom")
    monkeypatch.setenv("CUSTOM_MODEL", "deepseek-chat")
    monkeypatch.setenv("CUSTOM_BASE_URL", "https://api.deepseek.com/v1")
    config = ModelConfig.from_env(prefix="CUSTOM")
    assert config.api_key == "sk-custom"
    assert config.model == "deepseek-chat"
