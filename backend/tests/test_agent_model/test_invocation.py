"""Tests for model invocation policy.

Covers retryable/non-retryable failures, streaming parity, fallback ordering,
repair rejection, and model-aware compression thresholds.
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.agent.model.config import ModelConfig, RetryPolicy
from backend.agent.model.invocation import (
    InvocationPolicy,
    InvocationResult,
    _is_retryable_error,
    _compute_backoff_delay,
    repair_structured_output,
)
from backend.agent.model.messages import Message, ModelResponse, Role, TokenUsage, ToolUseBlock
from backend.agent.model.client import ModelClient


# --- Test helpers ---


class _MockModelClient(ModelClient):
    def __init__(self, responses: list[ModelResponse | Exception] | None = None):
        self._responses = list(responses or [])
        self._call_count = 0

    async def chat(self, messages, tool_schemas=None):
        if self._call_count >= len(self._responses):
            return ModelResponse(content="default", tool_use_blocks=[], token_usage=TokenUsage())
        resp = self._responses[self._call_count]
        self._call_count += 1
        if isinstance(resp, Exception):
            raise resp
        return resp

    async def chat_stream(self, messages, tool_schemas=None, on_text_delta=None):
        return await self.chat(messages, tool_schemas)


def _ok_response(content: str = '{"status":"ok"}') -> ModelResponse:
    return ModelResponse(content=content, tool_use_blocks=[], token_usage=TokenUsage(100, 50))


def _make_config(**kwargs) -> ModelConfig:
    defaults = {
        "api_key": "test-key",
        "model": "test-model",
        "retry_policy": RetryPolicy(max_retries=2, base_delay_ms=10, max_delay_ms=100),
        "request_timeout_ms": 5000,
    }
    defaults.update(kwargs)
    return ModelConfig(**defaults)


# --- Tests ---


class TestRetryableClassification:
    def test_timeout_is_retryable(self):
        assert _is_retryable_error(TimeoutError("request timed out")) is True

    def test_rate_limit_is_retryable(self):
        assert _is_retryable_error(Exception("Rate limit exceeded (429)")) is True

    def test_500_is_retryable(self):
        assert _is_retryable_error(Exception("Internal server error 500")) is True

    def test_auth_is_not_retryable(self):
        assert _is_retryable_error(Exception("authentication failed")) is False

    def test_401_is_not_retryable(self):
        assert _is_retryable_error(Exception("Unauthorized 401")) is False

    def test_404_is_not_retryable(self):
        assert _is_retryable_error(Exception("model_not_found 404")) is False

    def test_context_length_is_not_retryable(self):
        assert _is_retryable_error(Exception("context_length exceeded")) is False


class TestBackoffDelay:
    def test_delay_increases(self):
        rp = RetryPolicy(max_retries=3, base_delay_ms=1000, backoff_factor=2.0, jitter_fraction=0)
        d0 = _compute_backoff_delay(rp, 0)
        d1 = _compute_backoff_delay(rp, 1)
        d2 = _compute_backoff_delay(rp, 2)
        assert d0 < d1 < d2

    def test_delay_capped(self):
        rp = RetryPolicy(max_retries=10, base_delay_ms=1000, max_delay_ms=5000, backoff_factor=2.0, jitter_fraction=0)
        d = _compute_backoff_delay(rp, 100)
        assert d <= 5.0

    def test_jitter_adds_variance(self):
        rp = RetryPolicy(max_retries=3, base_delay_ms=1000, backoff_factor=2.0, jitter_fraction=0.5)
        delays = [_compute_backoff_delay(rp, 1) for _ in range(100)]
        # Should have some variance
        assert max(delays) - min(delays) > 0


class TestInvocationRetry:
    @pytest.mark.asyncio
    async def test_success_on_first_try(self):
        client = _MockModelClient([_ok_response()])
        policy = InvocationPolicy(client, _make_config())

        result = await policy.invoke([Message(role=Role.USER, content="test")])

        assert result.success is True
        assert len(result.attempts) == 1
        assert result.total_retries == 0

    @pytest.mark.asyncio
    async def test_retry_on_retryable_error(self):
        client = _MockModelClient([
            Exception("timeout"),
            _ok_response(),
        ])
        config = _make_config()
        policy = InvocationPolicy(client, config)

        result = await policy.invoke([Message(role=Role.USER, content="test")])

        assert result.success is True
        assert len(result.attempts) == 2
        assert result.total_retries == 1

    @pytest.mark.asyncio
    async def test_no_retry_on_non_retryable_error(self):
        client = _MockModelClient([
            Exception("authentication failed"),
            _ok_response(),
        ])
        policy = InvocationPolicy(client, _make_config())

        result = await policy.invoke([Message(role=Role.USER, content="test")])

        assert result.success is False
        assert len(result.attempts) == 1
        assert result.total_retries == 0


class TestFallbackOrdering:
    @pytest.mark.asyncio
    async def test_fallback_after_primary_exhausted(self):
        primary = _MockModelClient([Exception("timeout"), Exception("timeout"), Exception("timeout")])
        fallback = _MockModelClient([_ok_response("fallback response")])
        config = _make_config(fallback_models=("fallback-model",))
        policy = InvocationPolicy(primary, config, fallback_clients=[fallback])

        result = await policy.invoke([Message(role=Role.USER, content="test")])

        assert result.success is True
        assert result.final_model_id == "fallback-model"
        assert result.fallback_used is True

    @pytest.mark.asyncio
    async def test_fallback_ordering(self):
        primary = _MockModelClient([Exception("timeout")] * 3)
        fb1 = _MockModelClient([Exception("timeout")])
        fb2 = _MockModelClient([_ok_response("fb2 response")])
        config = _make_config(fallback_models=("fb1", "fb2"))
        policy = InvocationPolicy(primary, config, fallback_clients=[fb1, fb2])

        result = await policy.invoke([Message(role=Role.USER, content="test")])

        assert result.success is True
        assert result.final_model_id == "fb2"


class TestTelemetry:
    @pytest.mark.asyncio
    async def test_telemetry_no_prompts(self):
        """Telemetry should not expose prompts or secrets."""
        client = _MockModelClient([_ok_response()])
        policy = InvocationPolicy(client, _make_config())

        result = await policy.invoke([Message(role=Role.USER, content="secret prompt")])
        telemetry = result.to_telemetry()

        telemetry_str = str(telemetry)
        assert "secret prompt" not in telemetry_str
        assert "test-key" not in telemetry_str


class TestStructuredRepair:
    def test_repair_valid_json(self):
        """Valid JSON with required fields returns directly."""
        output = '{"status": "completed", "summary": "test"}'
        result = repair_structured_output(output, ["status", "summary"])
        assert result is not None
        assert result["status"] == "completed"

    def test_repair_code_block(self):
        """JSON in code block is extracted."""
        output = '```json\n{"status": "completed", "summary": "test"}\n```'
        result = repair_structured_output(output, ["status", "summary"])
        assert result is not None

    def test_repair_fails_on_missing_fields(self):
        """Returns None when required fields are missing."""
        output = '{"status": "completed"}'
        result = repair_structured_output(output, ["status", "summary"])
        assert result is None

    def test_repair_fails_on_invalid_json(self):
        """Returns None for completely invalid JSON."""
        output = "not json at all"
        result = repair_structured_output(output, ["status"])
        assert result is None


class TestCompressionThresholds:
    def test_model_aware_threshold(self):
        """Compression config uses model's context window."""
        from backend.agent.runtime.compression.config import CompressionConfig

        config = CompressionConfig.from_model_config(
            context_window_tokens=100_000,
            output_reserve=4096,
            compaction_reserve=8000,
        )
        # Threshold should be context_window - summary_buffer - auto_buffer
        assert config.auto_compact_threshold < 100_000
        assert config.auto_compact_threshold > 80_000

    def test_small_context_window(self):
        """Small context window produces reasonable threshold."""
        from backend.agent.runtime.compression.config import CompressionConfig

        config = CompressionConfig.from_model_config(
            context_window_tokens=32_000,
            output_reserve=2048,
            compaction_reserve=4000,
        )
        assert config.auto_compact_threshold < 32_000
        assert config.auto_compact_threshold > 20_000
