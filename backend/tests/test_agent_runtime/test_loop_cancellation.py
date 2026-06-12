from __future__ import annotations

import json
import pytest

from backend.agent.model.client import ModelClient
from backend.agent.model.messages import Message, ModelResponse, Role, TokenUsage, ToolUseBlock
from backend.agent.runtime.cancellation import CancellationProbe, Cancelled
from backend.agent.runtime.loop import run_loop
from backend.agent.tools.protocol import RiskLevel, Tool, ToolSchema
from backend.agent.tools.registry import ToolRegistry


class FakeModel(ModelClient):
    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self._call_count = 0

    async def chat(self, messages, tool_schemas=None):
        resp = self._responses[self._call_count]
        self._call_count += 1
        return resp


class EchoTool(Tool):
    @property
    def name(self) -> str: return "echo"
    @property
    def description(self) -> str: return "Echo"
    @property
    def input_schema(self) -> dict: return {"type": "object", "properties": {"text": {"type": "string"}}}
    @property
    def risk_level(self) -> RiskLevel: return RiskLevel.LOW
    @property
    def is_read_only(self) -> bool: return True
    @property
    def is_concurrency_safe(self) -> bool: return True
    async def call(self, input: dict) -> str:
        return input.get("text", "")


@pytest.mark.asyncio
async def test_cancellation_before_model_call_raises():
    probe = CancellationProbe()
    probe.cancel()
    model = FakeModel([ModelResponse(content="never", tool_use_blocks=[])])
    reg = ToolRegistry()
    with pytest.raises(Cancelled):
        await run_loop(
            model=model,
            tool_registry=reg,
            messages=[Message(role=Role.USER, content="hi")],
            cancellation_probe=probe,
        )


@pytest.mark.asyncio
async def test_cancellation_before_tool_execution_raises():
    probe = CancellationProbe()
    model = FakeModel([
        ModelResponse(content="calling", tool_use_blocks=[ToolUseBlock(tool_use_id="u1", name="echo", input={"text": "x"})]),
        ModelResponse(content="done", tool_use_blocks=[]),
    ])
    reg = ToolRegistry()
    reg.register(EchoTool())
    probe.cancel()
    with pytest.raises(Cancelled):
        await run_loop(
            model=model,
            tool_registry=reg,
            messages=[Message(role=Role.USER, content="test")],
            cancellation_probe=probe,
        )


@pytest.mark.asyncio
async def test_no_probe_allows_normal_execution():
    model = FakeModel([ModelResponse(content="ok", tool_use_blocks=[])])
    reg = ToolRegistry()
    result = await run_loop(
        model=model,
        tool_registry=reg,
        messages=[Message(role=Role.USER, content="hi")],
        cancellation_probe=None,
    )
    assert result.output == "ok"


@pytest.mark.asyncio
async def test_cancellation_between_steps_raises():
    call_count = 0
    probe = CancellationProbe()

    class ProbeCancellingModel(ModelClient):
        async def chat(self, messages, tool_schemas=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ModelResponse(content="step1", tool_use_blocks=[
                    ToolUseBlock(tool_use_id="u1", name="echo", input={"text": "x"})
                ])
            return ModelResponse(content="step2", tool_use_blocks=[])

    reg = ToolRegistry()
    reg.register(EchoTool())

    original_call = EchoTool.call

    async def cancelling_call(self, input):
        probe.cancel()
        return await original_call(self, input)

    EchoTool.call = cancelling_call
    try:
        with pytest.raises(Cancelled):
            await run_loop(
                model=ProbeCancellingModel(),
                tool_registry=reg,
                messages=[Message(role=Role.USER, content="test")],
                cancellation_probe=probe,
            )
    finally:
        EchoTool.call = original_call
