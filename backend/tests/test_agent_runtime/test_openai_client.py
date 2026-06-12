from __future__ import annotations

import json
import pytest
from typing import Any

import httpx

from backend.agent.model.config import ModelConfig
from backend.agent.model.messages import (
    MAX_VISIBLE_DELTA_CHARS,
    Message,
    Role,
    ToolUseBlock,
    ToolResultBlock,
    truncate_delta,
)
from backend.agent.model.openai_client import OpenAIModelClient, build_tools_param, _convert_message, _parse_response
from backend.agent.tools.protocol import ToolSchema


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


def _make_sse_chunks_text_only() -> list[bytes]:
    """Build SSE chunks for a streamed text-only response."""
    chunks = [
        {"id": "chatcmpl-s1", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]},
        {"id": "chatcmpl-s1", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": "Hello"}, "finish_reason": None}]},
        {"id": "chatcmpl-s1", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": " world"}, "finish_reason": None}]},
        {"id": "chatcmpl-s1", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": "!"}, "finish_reason": "stop"}]},
        {"id": "chatcmpl-s1", "object": "chat.completion.chunk", "choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 3}},
    ]
    lines = []
    for c in chunks:
        lines.append(f"data: {json.dumps(c)}\n\n")
    lines.append("data: [DONE]\n\n")
    return [l.encode() for l in lines]


def _make_sse_chunks_tool_call() -> list[bytes]:
    """Build SSE chunks for a streamed tool-call response with incrementally assembled arguments."""
    chunks = [
        {"id": "chatcmpl-s2", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]},
        {"id": "chatcmpl-s2", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "call_xyz", "type": "function", "function": {"name": "search", "arguments": ""}}]}, "finish_reason": None}]},
        {"id": "chatcmpl-s2", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{\"qu"}}]}, "finish_reason": None}]},
        {"id": "chatcmpl-s2", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "ery\": \"test\"}"}}]}, "finish_reason": None}]},
        {"id": "chatcmpl-s2", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
    ]
    lines = []
    for c in chunks:
        lines.append(f"data: {json.dumps(c)}\n\n")
    lines.append("data: [DONE]\n\n")
    return [l.encode() for l in lines]


def _make_sse_chunks_with_reasoning() -> list[bytes]:
    """Build SSE chunks that include reasoning fields that must NOT be emitted as visible deltas."""
    chunks = [
        {"id": "chatcmpl-s3", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]},
        {"id": "chatcmpl-s3", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"reasoning_content": "Let me think about this..."}, "finish_reason": None}]},
        {"id": "chatcmpl-s3", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": "The answer is 42."}, "finish_reason": None}]},
        {"id": "chatcmpl-s3", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"reasoning_content": "Because of X and Y."}, "finish_reason": None}]},
        {"id": "chatcmpl-s3", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    lines = []
    for c in chunks:
        lines.append(f"data: {json.dumps(c)}\n\n")
    lines.append("data: [DONE]\n\n")
    return [l.encode() for l in lines]


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


class MockStreamTransport(httpx.AsyncBaseTransport):
    """Mock transport that returns chunked SSE responses for streaming."""
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.captured_requests: list[dict[str, Any]] = []

    async def handle_async_request(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        body = json.loads(await request.aread())
        self.captured_requests.append(body)

        class _ByteStream(httpx.AsyncByteStream):
            def __init__(self, data: list[bytes]):
                self._data = data

            async def __aiter__(self):
                for chunk in self._data:
                    yield chunk

            async def aclose(self):
                pass

        return httpx.Response(
            status_code=200,
            stream=_ByteStream(self._chunks),
            request=request,
        )


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


@pytest.mark.asyncio
async def test_chat_retries_rate_limit_response():
    rate_limited = httpx.Response(
        status_code=429,
        json={"error": {"message": "slow down"}},
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
    )
    transport = MockTransport([rate_limited, _mock_response(SIMPLE_RESPONSE)])
    client = httpx.AsyncClient(transport=transport)
    model = OpenAIModelClient(
        config=_make_config(),
        http_client=client,
        retry_base_delay=0,
    )

    result = await model.chat([Message(role=Role.USER, content="hi")])

    assert result.content == "Hello!"
    assert len(transport.captured_requests) == 2


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


# --- Task 1.7: Model client tests for streamed text-only responses ---

@pytest.mark.asyncio
async def test_stream_text_only():
    """chat_stream accumulates visible content and returns final ModelResponse."""
    chunks = _make_sse_chunks_text_only()
    transport = MockStreamTransport(chunks)
    client = httpx.AsyncClient(transport=transport)
    config = _make_config()
    model = OpenAIModelClient(config=config, http_client=client)

    deltas: list[str] = []
    result = await model.chat_stream(
        [Message(role=Role.USER, content="hi")],
        on_text_delta=lambda t: deltas.append(t),
    )

    assert result.content == "Hello world!"
    assert result.tool_use_blocks == []
    assert result.token_usage.input_tokens == 5
    assert result.token_usage.output_tokens == 3
    assert deltas == ["Hello", " world", "!"]


@pytest.mark.asyncio
async def test_stream_text_only_no_callback():
    """chat_stream works without on_text_delta callback."""
    chunks = _make_sse_chunks_text_only()
    transport = MockStreamTransport(chunks)
    client = httpx.AsyncClient(transport=transport)
    config = _make_config()
    model = OpenAIModelClient(config=config, http_client=client)

    result = await model.chat_stream([Message(role=Role.USER, content="hi")])
    assert result.content == "Hello world!"


# --- Task 1.8: Model client tests for streamed tool-call responses ---

@pytest.mark.asyncio
async def test_stream_tool_call():
    """chat_stream incrementally assembles tool-call arguments."""
    chunks = _make_sse_chunks_tool_call()
    transport = MockStreamTransport(chunks)
    client = httpx.AsyncClient(transport=transport)
    config = _make_config()
    model = OpenAIModelClient(config=config, http_client=client)

    result = await model.chat_stream([Message(role=Role.USER, content="search for test")])

    assert result.content == ""
    assert len(result.tool_use_blocks) == 1
    tc = result.tool_use_blocks[0]
    assert tc.tool_use_id == "call_xyz"
    assert tc.name == "search"
    assert tc.input == {"query": "test"}


# --- Task 1.9: Tests proving reasoning fields are not emitted as visible deltas ---

@pytest.mark.asyncio
async def test_stream_excludes_reasoning_fields():
    """chat_stream does NOT emit reasoning_content as visible text deltas."""
    chunks = _make_sse_chunks_with_reasoning()
    transport = MockStreamTransport(chunks)
    client = httpx.AsyncClient(transport=transport)
    config = _make_config()
    model = OpenAIModelClient(config=config, http_client=client)

    deltas: list[str] = []
    result = await model.chat_stream(
        [Message(role=Role.USER, content="think about it")],
        on_text_delta=lambda t: deltas.append(t),
    )

    # Only visible content should be in deltas, not reasoning_content
    assert deltas == ["The answer is 42."]
    assert result.content == "The answer is 42."
    # Reasoning text must NOT appear in content
    assert "Let me think" not in result.content
    assert "Because of X" not in result.content


# --- Task 1.10: Tests proving non-streaming fallback remains compatible ---

@pytest.mark.asyncio
async def test_chat_stream_fallback_to_chat():
    """ModelClient.chat_stream defaults to chat() for non-streaming clients."""
    from backend.agent.model.client import ModelClient

    class NonStreamingClient(ModelClient):
        def __init__(self, response: ModelResponse):
            self._response = response

        async def chat(self, messages, tool_schemas=None):
            return self._response

    from backend.agent.model.messages import ModelResponse, TokenUsage
    expected = ModelResponse(content="fallback", token_usage=TokenUsage(input_tokens=1, output_tokens=1))
    client = NonStreamingClient(expected)

    # chat_stream should fall back to chat()
    result = await client.chat_stream([Message(role=Role.USER, content="hi")])
    assert result.content == "fallback"
    assert result.token_usage.input_tokens == 1


def test_truncate_delta_short():
    assert truncate_delta("hello", 10) == "hello"


def test_truncate_delta_long():
    text = "x" * 2000
    result = truncate_delta(text, MAX_VISIBLE_DELTA_CHARS)
    assert len(result) == MAX_VISIBLE_DELTA_CHARS


def test_truncate_delta_exact():
    text = "x" * MAX_VISIBLE_DELTA_CHARS
    result = truncate_delta(text, MAX_VISIBLE_DELTA_CHARS)
    assert result == text
