from __future__ import annotations

import pytest
from typing import Any

from backend.agent_runtime.model.messages import Message, ModelResponse, Role, TokenUsage, ToolUseBlock
from backend.agent_runtime.model.client import ModelClient
from backend.agent_runtime.runtime.loop import run_loop
from backend.agent_runtime.tool.registry import ToolRegistry
from backend.agent_runtime.tool.protocol import RiskLevel, Tool


class FakeModelClient(ModelClient):
    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self._call_count = 0

    async def chat(self, messages: list[Message]) -> ModelResponse:
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
    assert "max steps" in result.output.lower()


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

    async def chat(self, messages: list[Message]) -> ModelResponse:
        self.captured_messages.append(list(messages))
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
