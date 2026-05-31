from __future__ import annotations

import pytest
from typing import Any

from backend.agent.model.messages import Message, ModelResponse, Role, TokenUsage, ToolUseBlock
from backend.agent.model.client import ModelClient
from backend.agent.runtime.loop import run_loop
from backend.agent.tools.registry import ToolRegistry
from backend.agent.tools.protocol import RiskLevel, Tool, ToolSchema


class FakeModelClient(ModelClient):
    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self._call_count = 0

    async def chat(self, messages: list[Message], tool_schemas: list[ToolSchema] | None = None) -> ModelResponse:
        resp = self._responses[self._call_count]
        self._call_count += 1
        return resp


class EchoTool(Tool):
    @property
    def name(self) -> str: return "echo"
    @property
    def description(self) -> str: return "Echo input back"
    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"text": {"type": "string"}}}
    @property
    def risk_level(self) -> RiskLevel: return RiskLevel.LOW
    @property
    def is_read_only(self) -> bool: return True
    @property
    def is_concurrency_safe(self) -> bool: return True
    async def call(self, input: dict[str, Any]) -> str:
        return input.get("text", "")


@pytest.mark.asyncio
async def test_final_only_response():
    model = FakeModelClient([ModelResponse(content="final answer", tool_use_blocks=[])])
    reg = ToolRegistry()
    result = await run_loop(model=model, tool_registry=reg, messages=[Message(role=Role.USER, content="hi")])
    assert result.output == "final answer"
    assert result.stopped_by_max_steps is False
    assert len(result.steps) == 1
    assert result.steps[0].kind.value == "final"


@pytest.mark.asyncio
async def test_tool_call_then_final():
    model = FakeModelClient([
        ModelResponse(content="let me call echo", tool_use_blocks=[ToolUseBlock(tool_use_id="u1", name="echo", input={"text": "hello"})]),
        ModelResponse(content="echoed hello", tool_use_blocks=[]),
    ])
    reg = ToolRegistry()
    reg.register(EchoTool())
    result = await run_loop(model=model, tool_registry=reg, messages=[Message(role=Role.USER, content="call echo")])
    assert result.output == "echoed hello"
    assert result.stopped_by_max_steps is False
    assert [s.kind.value for s in result.steps] == ["think", "call", "observe", "final"]


@pytest.mark.asyncio
async def test_max_steps_stops_loop():
    model = FakeModelClient([
        ModelResponse(content="calling again", tool_use_blocks=[ToolUseBlock(tool_use_id=f"u{i}", name="echo", input={"text": str(i)})])
        for i in range(5)
    ])
    reg = ToolRegistry()
    reg.register(EchoTool())
    result = await run_loop(model=model, tool_registry=reg, messages=[Message(role=Role.USER, content="loop")], max_steps=3)
    assert result.stopped_by_max_steps is True
    # Output is either salvaged JSON from reminder or the default "max steps" message
    assert "max steps" in result.output.lower() or "status" in result.output.lower()


@pytest.mark.asyncio
async def test_unknown_tool_returns_error():
    model = FakeModelClient([
        ModelResponse(content="calling unknown", tool_use_blocks=[ToolUseBlock(tool_use_id="u1", name="nonexistent", input={})]),
        ModelResponse(content="got error", tool_use_blocks=[]),
    ])
    reg = ToolRegistry()
    result = await run_loop(model=model, tool_registry=reg, messages=[Message(role=Role.USER, content="test")])
    assert result.output == "got error"
    observe_steps = [s for s in result.steps if s.kind.value == "observe"]
    assert len(observe_steps) == 1
    assert observe_steps[0].is_error is True
    assert "Unknown tool" in observe_steps[0].output


@pytest.mark.asyncio
async def test_multiple_tool_calls():
    model = FakeModelClient([
        ModelResponse(content="calling two", tool_use_blocks=[
            ToolUseBlock(tool_use_id="u1", name="echo", input={"text": "a"}),
            ToolUseBlock(tool_use_id="u2", name="echo", input={"text": "b"}),
        ]),
        ModelResponse(content="done", tool_use_blocks=[]),
    ])
    reg = ToolRegistry()
    reg.register(EchoTool())
    result = await run_loop(model=model, tool_registry=reg, messages=[Message(role=Role.USER, content="multi")])
    assert result.output == "done"
    assert len([s for s in result.steps if s.kind.value == "call"]) == 2
    assert len([s for s in result.steps if s.kind.value == "observe"]) == 2


@pytest.mark.asyncio
async def test_token_usage_accumulated():
    model = FakeModelClient([
        ModelResponse(content="step1", tool_use_blocks=[ToolUseBlock(tool_use_id="u1", name="echo", input={"text": "x"})], token_usage=TokenUsage(input_tokens=10, output_tokens=5)),
        ModelResponse(content="final", tool_use_blocks=[], token_usage=TokenUsage(input_tokens=20, output_tokens=8)),
    ])
    reg = ToolRegistry()
    reg.register(EchoTool())
    result = await run_loop(model=model, tool_registry=reg, messages=[Message(role=Role.USER, content="usage")])
    assert result.token_usage.input_tokens == 30
    assert result.token_usage.output_tokens == 13


class CapturingModelClient(ModelClient):
    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self._call_count = 0
        self.captured_messages: list[list[Message]] = []
        self.captured_schemas: list[list[ToolSchema] | None] = []

    async def chat(self, messages: list[Message], tool_schemas: list[ToolSchema] | None = None) -> ModelResponse:
        self.captured_messages.append(list(messages))
        self.captured_schemas.append(tool_schemas)
        resp = self._responses[self._call_count]
        self._call_count += 1
        return resp


@pytest.mark.asyncio
async def test_assistant_message_includes_tool_use_blocks():
    model = CapturingModelClient([
        ModelResponse(content="thinking", tool_use_blocks=[ToolUseBlock(tool_use_id="u1", name="echo", input={"text": "hi"})]),
        ModelResponse(content="done", tool_use_blocks=[]),
    ])
    reg = ToolRegistry()
    reg.register(EchoTool())
    await run_loop(model=model, tool_registry=reg, messages=[Message(role=Role.USER, content="test")])
    second_call = model.captured_messages[1]
    assistant_msgs = [m for m in second_call if m.role == Role.ASSISTANT]
    assert len(assistant_msgs) >= 1
    last_assistant = assistant_msgs[-1]
    assert isinstance(last_assistant.content, list)
    assert any(isinstance(b, ToolUseBlock) and b.tool_use_id == "u1" for b in last_assistant.content)


@pytest.mark.asyncio
async def test_model_receives_tool_schemas_from_registry():
    model = CapturingModelClient([ModelResponse(content="done", tool_use_blocks=[])])
    reg = ToolRegistry()
    reg.register(EchoTool())
    await run_loop(model=model, tool_registry=reg, messages=[Message(role=Role.USER, content="test")])
    schemas = model.captured_schemas[0]
    assert schemas is not None
    assert len(schemas) == 1
    assert schemas[0].name == "echo"


@pytest.mark.asyncio
async def test_empty_registry_passes_empty_schemas():
    model = CapturingModelClient([ModelResponse(content="done", tool_use_blocks=[])])
    reg = ToolRegistry()
    await run_loop(model=model, tool_registry=reg, messages=[Message(role=Role.USER, content="test")])
    schemas = model.captured_schemas[0]
    assert schemas is not None
    assert len(schemas) == 0


# --- Task 2.6: Runtime loop tests for ordered visible delta callbacks ---

class StreamingFakeModelClient(ModelClient):
    """Fake model that simulates streaming via chat_stream."""
    def __init__(self, response: ModelResponse, deltas_to_emit: list[str] | None = None) -> None:
        self._response = response
        self._deltas_to_emit = deltas_to_emit or []
        self.chat_stream_called = False

    async def chat(self, messages: list[Message], tool_schemas: list[ToolSchema] | None = None) -> ModelResponse:
        return self._response

    async def chat_stream(
        self,
        messages: list[Message],
        tool_schemas: list[ToolSchema] | None = None,
        on_text_delta=None,
    ) -> ModelResponse:
        self.chat_stream_called = True
        for delta in self._deltas_to_emit:
            if on_text_delta:
                on_text_delta(delta)
        return self._response


@pytest.mark.asyncio
async def test_on_text_delta_receives_ordered_deltas():
    """on_text_delta callback receives deltas in order during streaming."""
    model = StreamingFakeModelClient(
        ModelResponse(content="Hello world!", tool_use_blocks=[]),
        deltas_to_emit=["Hello", " world", "!"],
    )
    reg = ToolRegistry()
    received_deltas: list[str] = []
    result = await run_loop(
        model=model,
        tool_registry=reg,
        messages=[Message(role=Role.USER, content="hi")],
        on_text_delta=lambda t: received_deltas.append(t),
    )
    assert received_deltas == ["Hello", " world", "!"]
    assert result.output == "Hello world!"
    assert model.chat_stream_called is True


@pytest.mark.asyncio
async def test_no_on_text_delta_uses_chat():
    """When on_text_delta is None, run_loop uses chat() not chat_stream()."""
    model = StreamingFakeModelClient(
        ModelResponse(content="no stream", tool_use_blocks=[]),
    )
    reg = ToolRegistry()
    result = await run_loop(
        model=model,
        tool_registry=reg,
        messages=[Message(role=Role.USER, content="hi")],
    )
    assert result.output == "no stream"
    assert model.chat_stream_called is False


@pytest.mark.asyncio
async def test_on_text_delta_with_tool_calls():
    """on_text_delta fires for visible content even when tool calls are made."""
    model = StreamingFakeModelClient(
        ModelResponse(
            content="calling tool",
            tool_use_blocks=[ToolUseBlock(tool_use_id="u1", name="echo", input={"text": "x"})],
        ),
        deltas_to_emit=["calling tool"],
    )
    # Second response after tool call
    model2 = StreamingFakeModelClient(
        ModelResponse(content="done", tool_use_blocks=[]),
        deltas_to_emit=["done"],
    )

    class TwoStepModel(ModelClient):
        def __init__(self):
            self._step = 0
            self._models = [model, model2]

        async def chat(self, messages, tool_schemas=None):
            return await self._models[self._step].chat(messages, tool_schemas)

        async def chat_stream(self, messages, tool_schemas=None, on_text_delta=None):
            m = self._models[self._step]
            self._step += 1
            return await m.chat_stream(messages, tool_schemas, on_text_delta)

    reg = ToolRegistry()
    reg.register(EchoTool())
    deltas: list[str] = []
    result = await run_loop(
        model=TwoStepModel(),
        tool_registry=reg,
        messages=[Message(role=Role.USER, content="test")],
        on_text_delta=lambda t: deltas.append(t),
    )
    assert "calling tool" in deltas
    assert "done" in deltas
    assert result.output == "done"
