"""Unit tests for ToolExecutor.

Tests cover:
- Valid tool calls
- Malformed arguments
- Unknown tools
- Denied tools (allowlist)
- Timeouts
- Cancellation
- Oversized observations
- Budget charging
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.agent.model.messages import ToolUseBlock
from backend.agent.runtime.accounting import TaskAccounting
from backend.agent.tools.executor import ToolExecutor, ToolExecutorConfig, ToolObservation
from backend.agent.tools.protocol import Tool, ToolSchema, RiskLevel
from backend.agent.tools.registry import ToolRegistry


# --- Test helpers ---


class _MockTool(Tool):
    def __init__(
        self,
        name: str = "test_tool",
        description: str = "A test tool",
        output: str = "test output",
        is_error: bool = False,
        delay: float = 0,
        raise_exc: Exception | None = None,
    ):
        self._name = name
        self._description = description
        self._output = output
        self._is_error = is_error
        self._delay = delay
        self._raise_exc = raise_exc

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        }

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.LOW

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def is_concurrency_safe(self) -> bool:
        return True

    async def call(self, input: dict) -> str:
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        if self._raise_exc:
            raise self._raise_exc
        return self._output


def _make_block(name: str = "test_tool", input: dict | None = None, tool_use_id: str = "call_1") -> ToolUseBlock:
    return ToolUseBlock(
        tool_use_id=tool_use_id,
        name=name,
        input=input or {"query": "test"},
    )


def _make_registry(tools: list[Tool] | None = None) -> ToolRegistry:
    registry = ToolRegistry()
    for t in (tools or [_MockTool()]):
        registry.register(t)
    return registry


# --- Tests ---


class TestValidCalls:
    @pytest.mark.asyncio
    async def test_valid_call_returns_observation(self):
        """Valid tool call returns a successful observation."""
        registry = _make_registry()
        executor = ToolExecutor(registry=registry)
        block = _make_block()

        obs = await executor.execute(block)

        assert obs.is_error is False
        assert obs.content == "test output"
        assert obs.tool_use_id == "call_1"
        assert obs.tool_name == "test_tool"

    @pytest.mark.asyncio
    async def test_valid_call_charges_budget(self):
        """Valid tool call charges observation to task accounting."""
        registry = _make_registry()
        accounting = TaskAccounting(task_id="t1")
        executor = ToolExecutor(registry=registry, task_accounting=accounting)
        block = _make_block()

        await executor.execute(block)

        assert accounting.observation_tokens > 0
        assert accounting.elapsed_ms >= 0


class TestMalformedArguments:
    @pytest.mark.asyncio
    async def test_missing_required_field_returns_error(self):
        """Missing required field returns schema_validation error."""
        registry = _make_registry()
        executor = ToolExecutor(registry=registry)
        block = _make_block(input={"limit": 5})  # Missing required "query"

        obs = await executor.execute(block)

        assert obs.is_error is True
        assert obs.error_kind == "schema_validation"

    @pytest.mark.asyncio
    async def test_non_dict_input_returns_error(self):
        """Non-dict input returns schema_validation error."""
        registry = _make_registry()
        executor = ToolExecutor(registry=registry)
        block = ToolUseBlock(tool_use_id="call_1", name="test_tool", input="not a dict")

        obs = await executor.execute(block)

        assert obs.is_error is True
        assert obs.error_kind == "schema_validation"

    @pytest.mark.asyncio
    async def test_type_coercion_string(self):
        """String values are coerced to the expected type."""
        registry = _make_registry()
        executor = ToolExecutor(registry=registry)
        block = _make_block(input={"query": 123})  # int instead of string

        obs = await executor.execute(block)

        # Should succeed after coercion
        assert obs.is_error is False


class TestUnknownTools:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        """Unknown tool returns unknown_tool error."""
        registry = _make_registry()
        executor = ToolExecutor(registry=registry)
        block = _make_block(name="nonexistent_tool")

        obs = await executor.execute(block)

        assert obs.is_error is True
        assert obs.error_kind == "unknown_tool"
        assert "nonexistent_tool" in obs.content


class TestDeniedTools:
    @pytest.mark.asyncio
    async def test_denied_tool_returns_error(self):
        """Tool not in allowlist returns denied error."""
        registry = _make_registry()
        config = ToolExecutorConfig(allowed_tools={"other_tool"})
        executor = ToolExecutor(registry=registry, config=config)
        block = _make_block()

        obs = await executor.execute(block)

        assert obs.is_error is True
        assert obs.error_kind == "denied"

    @pytest.mark.asyncio
    async def test_allowed_tool_succeeds(self):
        """Tool in allowlist succeeds."""
        registry = _make_registry()
        config = ToolExecutorConfig(allowed_tools={"test_tool"})
        executor = ToolExecutor(registry=registry, config=config)
        block = _make_block()

        obs = await executor.execute(block)

        assert obs.is_error is False

    @pytest.mark.asyncio
    async def test_none_allowlist_allows_all(self):
        """None allowlist allows all registered tools."""
        registry = _make_registry()
        config = ToolExecutorConfig(allowed_tools=None)
        executor = ToolExecutor(registry=registry, config=config)
        block = _make_block()

        obs = await executor.execute(block)

        assert obs.is_error is False


class TestTimeouts:
    @pytest.mark.asyncio
    async def test_timeout_returns_error(self):
        """Tool that exceeds timeout returns timeout error."""
        tool = _MockTool(delay=2.0)
        registry = _make_registry([tool])
        config = ToolExecutorConfig(tool_timeout_s=0.1)
        executor = ToolExecutor(registry=registry, config=config)
        block = _make_block()

        obs = await executor.execute(block)

        assert obs.is_error is True
        assert obs.error_kind == "timeout"
        assert "timed out" in obs.content


class TestExceptions:
    @pytest.mark.asyncio
    async def test_tool_exception_returns_error(self):
        """Tool that raises exception returns exception error."""
        tool = _MockTool(raise_exc=ValueError("something went wrong"))
        registry = _make_registry([tool])
        executor = ToolExecutor(registry=registry)
        block = _make_block()

        obs = await executor.execute(block)

        assert obs.is_error is True
        assert obs.error_kind == "exception"
        assert "something went wrong" in obs.content


class TestObservationTruncation:
    @pytest.mark.asyncio
    async def test_oversized_observation_is_truncated(self):
        """Observations exceeding char limit are truncated."""
        tool = _MockTool(output="x" * 50000)
        registry = _make_registry([tool])
        config = ToolExecutorConfig(max_observation_chars=1000)
        executor = ToolExecutor(registry=registry, config=config)
        block = _make_block()

        obs = await executor.execute(block)

        assert len(obs.content) < 50000
        assert "[Observation truncated" in obs.content

    @pytest.mark.asyncio
    async def test_normal_observation_not_truncated(self):
        """Normal observations are not truncated."""
        tool = _MockTool(output="short output")
        registry = _make_registry([tool])
        config = ToolExecutorConfig(max_observation_chars=1000)
        executor = ToolExecutor(registry=registry, config=config)
        block = _make_block()

        obs = await executor.execute(block)

        assert obs.content == "short output"
        assert "[Observation truncated" not in obs.content


class _WriteMockTool(_MockTool):
    """A non-read-only mock tool that requires consent."""
    @property
    def is_read_only(self) -> bool:
        return False

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.MEDIUM


class TestConsent:
    @pytest.mark.asyncio
    async def test_denied_consent_returns_error(self):
        """Denied consent returns denied error."""
        tool = _WriteMockTool()
        registry = _make_registry([tool])

        async def deny_consent(t, inp):
            return False

        executor = ToolExecutor(registry=registry, consent_fn=deny_consent)
        block = _make_block()

        obs = await executor.execute(block)

        assert obs.is_error is True
        assert obs.error_kind == "denied"
        assert "consent" in obs.content.lower()


class TestEventCallbacks:
    @pytest.mark.asyncio
    async def test_tool_call_event_emitted(self):
        """Tool call event is emitted to callback."""
        registry = _make_registry()
        call_events = []
        result_events = []
        executor = ToolExecutor(
            registry=registry,
            on_tool_call=lambda e: call_events.append(e),
            on_tool_result=lambda e: result_events.append(e),
        )
        block = _make_block()

        await executor.execute(block)

        assert len(call_events) == 1
        assert call_events[0]["tool_name"] == "test_tool"
        assert len(result_events) == 1
        assert result_events[0]["is_error"] is False


class TestBudgetCharging:
    @pytest.mark.asyncio
    async def test_search_tool_charges_search_count(self):
        """Search tools increment search_count in accounting."""
        tool = _MockTool(name="search_repo")
        registry = _make_registry([tool])
        accounting = TaskAccounting(task_id="t1")
        executor = ToolExecutor(registry=registry, task_accounting=accounting)
        block = _make_block(name="search_repo")

        await executor.execute(block)

        assert accounting.search_count == 1

    @pytest.mark.asyncio
    async def test_file_tool_charges_file_read_count(self):
        """File read tools increment file_read_count in accounting."""
        tool = _MockTool(name="read_repo_file")
        registry = _make_registry([tool])
        accounting = TaskAccounting(task_id="t1")
        executor = ToolExecutor(registry=registry, task_accounting=accounting)
        block = _make_block(name="read_repo_file")

        await executor.execute(block)

        assert accounting.file_read_count == 1
