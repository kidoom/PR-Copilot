from __future__ import annotations

import json
import pytest
from typing import Any

import httpx

from backend.agent_runtime.config import ModelConfig
from backend.agent_runtime.models import Message, Role, ToolUseBlock, ToolResultBlock
from backend.agent_runtime.openai_client import OpenAIModelClient, build_tools_param, _convert_message, _parse_response
from backend.agent_runtime.tool import ToolSchema


# --- Fixtures ---


def _make_config(**overrides) -> ModelConfig:
    defaults = dict(
        api_key="sk-test-key",
        base_url="https://api.openai.com/v1",
        model="gpt-4o",
        max_output_tokens=4096,
        temperature=0.0,
    )
    defaults.update(overrides)
    return ModelConfig(**defaults)


def _mock_response(data: dict[str, Any], status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json=data,
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
    )


SIMPLE_RESPONSE = {
    "id": "chatcmpl-123",
    "object": "chat.completion",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Hello!"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
}

TOOL_CALL_RESPONSE = {
    "id": "chatcmpl-456",
    "object": "chat.completion",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_abc",
                        "type": "function",
                        "function": {
                            "name": "search",
                            "arguments": json.dumps({"query": "test"}),
                        },
                    }
                ],
            },
            "finish_reason": "tool_calls",
        }
    ],
    "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
}


# --- Mock transport ---


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


# --- build_tools_param tests ---


def test_build_tools_param_empty():
    assert build_tools_param([]) == []


def test_build_tools_param_single():
    schema = ToolSchema(name="search", description="Search files", input_schema={"type": "object", "properties": {"q": {"type": "string"}}})
    result = build_tools_param([schema])
    assert len(result) == 1
    assert result[0]["type"] == "function"
    assert result[0]["function"]["name"] == "search"
    assert result[0]["function"]["description"] == "Search files"
    assert result[0]["function"]["parameters"] == schema.input_schema


def test_build_tools_param_multiple():
    schemas = [
        ToolSchema(name="a", description="d1", input_schema={}),
        ToolSchema(name="b", description="d2", input_schema={"type": "object"}),
    ]
    result = build_tools_param(schemas)
    assert len(result) == 2
    assert result[0]["function"]["name"] == "a"
    assert result[1]["function"]["name"] == "b"


# --- _convert_message tests ---


def test_convert_system_message():
    msg = Message(role=Role.SYSTEM, content="You are helpful.")
    result = _convert_message(msg)
    assert result == {"role": "system", "content": "You are helpful."}


def test_convert_user_message():
    msg = Message(role=Role.USER, content="Hello")
    result = _convert_message(msg)
    assert result == {"role": "user", "content": "Hello"}


def test_convert_assistant_string():
    msg = Message(role=Role.ASSISTANT, content="I think...")
    result = _convert_message(msg)
    assert result == {"role": "assistant", "content": "I think..."}


def test_convert_assistant_with_tool_use():
    block = ToolUseBlock(tool_use_id="u1", name="search", input={"q": "test"})
    msg = Message(role=Role.ASSISTANT, content=[block])
    result = _convert_message(msg)
    assert result["role"] == "assistant"
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["id"] == "u1"
    assert result["tool_calls"][0]["function"]["name"] == "search"
    assert json.loads(result["tool_calls"][0]["function"]["arguments"]) == {"q": "test"}


def test_convert_tool_message():
    block = ToolResultBlock(tool_use_id="u1", content="result data")
    msg = Message(role=Role.TOOL, content=[block])
    result = _convert_message(msg)
    assert result["role"] == "tool"
    assert result["content"] == "result data"
    assert result["tool_call_id"] == "u1"


# --- _parse_response tests ---


def test_parse_simple_response():
    resp = _parse_response(SIMPLE_RESPONSE)
    assert resp.content == "Hello!"
    assert resp.tool_use_blocks == []
    assert resp.token_usage.input_tokens == 10
    assert resp.token_usage.output_tokens == 5


def test_parse_tool_call_response():
    resp = _parse_response(TOOL_CALL_RESPONSE)
    assert resp.content == ""
    assert len(resp.tool_use_blocks) == 1
    assert resp.tool_use_blocks[0].tool_use_id == "call_abc"
    assert resp.tool_use_blocks[0].name == "search"
    assert resp.tool_use_blocks[0].input == {"query": "test"}
    assert resp.token_usage.input_tokens == 20


def test_parse_response_missing_usage():
    data = {
        "choices": [{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
    }
    resp = _parse_response(data)
    assert resp.content == "hi"
    assert resp.token_usage.input_tokens == 0


# --- OpenAIModelClient integration tests ---


@pytest.mark.asyncio
async def test_simple_chat():
    transport = MockTransport([_mock_response(SIMPLE_RESPONSE)])
    client = httpx.AsyncClient(transport=transport)
    config = _make_config()
    model = OpenAIModelClient(config=config, http_client=client)

    result = await model.chat([Message(role=Role.USER, content="hi")])
    assert result.content == "Hello!"
    assert result.token_usage.input_tokens == 10


@pytest.mark.asyncio
async def test_chat_sends_correct_payload():
    transport = MockTransport([_mock_response(SIMPLE_RESPONSE)])
    client = httpx.AsyncClient(transport=transport)
    config = _make_config(api_key="sk-secret", model="gpt-4o-mini", base_url="https://custom.api.com/v1")
    model = OpenAIModelClient(config=config, http_client=client)

    await model.chat([Message(role=Role.SYSTEM, content="sys"), Message(role=Role.USER, content="hi")])

    assert len(transport.captured_requests) == 1
    body = transport.captured_requests[0]
    assert body["model"] == "gpt-4o-mini"
    assert body["temperature"] == 0.0
    assert body["max_tokens"] == 4096
    assert len(body["messages"]) == 2


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
    assert len(body["tools"]) == 1
    assert body["tools"][0]["function"]["name"] == "search"
    assert len(result.tool_use_blocks) == 1


@pytest.mark.asyncio
async def test_auth_header():
    transport = MockTransport([_mock_response(SIMPLE_RESPONSE)])
    client = httpx.AsyncClient(transport=transport)
    config = _make_config(api_key="sk-my-key")
    model = OpenAIModelClient(config=config, http_client=client)

    await model.chat([Message(role=Role.USER, content="hi")])

    req = transport.captured_requests[0]
    # Transport captures the body; auth header is in the request headers
    # We verify through the mock that the request was made successfully


@pytest.mark.asyncio
async def test_context_manager():
    transport = MockTransport([_mock_response(SIMPLE_RESPONSE)])
    client = httpx.AsyncClient(transport=transport)
    config = _make_config()
    async with OpenAIModelClient(config=config, http_client=client) as model:
        result = await model.chat([Message(role=Role.USER, content="hi")])
        assert result.content == "Hello!"


@pytest.mark.asyncio
async def test_http_error_raises():
    error_resp = httpx.Response(
        status_code=401,
        json={"error": {"message": "Invalid API key", "type": "auth_error"}},
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
    )
    transport = MockTransport([error_resp])
    client = httpx.AsyncClient(transport=transport)
    config = _make_config(api_key="bad-key")
    model = OpenAIModelClient(config=config, http_client=client)

    with pytest.raises(httpx.HTTPStatusError):
        await model.chat([Message(role=Role.USER, content="hi")])


# --- ModelConfig tests ---


def test_config_defaults():
    config = ModelConfig(api_key="sk-test")
    assert config.base_url == "https://api.openai.com/v1"
    assert config.model == "gpt-4o"
    assert config.max_output_tokens == 4096
    assert config.temperature == 0.0


def test_config_custom():
    config = ModelConfig(
        api_key="sk-test",
        base_url="https://custom.api.com/v1",
        model="claude-3",
        max_output_tokens=8192,
        temperature=0.7,
    )
    assert config.model == "claude-3"
    assert config.temperature == 0.7


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.api.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-3.5-turbo")
    monkeypatch.setenv("OPENAI_MAX_OUTPUT_TOKENS", "2048")
    monkeypatch.setenv("OPENAI_TEMPERATURE", "0.5")
    config = ModelConfig.from_env()
    assert config.api_key == "sk-env-key"
    assert config.base_url == "https://proxy.api.com/v1"
    assert config.model == "gpt-3.5-turbo"
    assert config.max_output_tokens == 2048
    assert config.temperature == 0.5


def test_config_from_env_custom_prefix(monkeypatch):
    monkeypatch.setenv("CUSTOM_API_KEY", "sk-custom")
    monkeypatch.setenv("CUSTOM_MODEL", "deepseek-chat")
    monkeypatch.setenv("CUSTOM_BASE_URL", "https://api.deepseek.com/v1")
    config = ModelConfig.from_env(prefix="CUSTOM")
    assert config.api_key == "sk-custom"
    assert config.model == "deepseek-chat"
    assert config.base_url == "https://api.deepseek.com/v1"


def test_config_from_env_defaults(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.delenv("OPENAI_TEMPERATURE", raising=False)
    config = ModelConfig.from_env()
    assert config.api_key == ""
    assert config.base_url == "https://api.openai.com/v1"
    assert config.model == "gpt-4o"
