"""Provider-neutral model invocation policy.

Provides a unified invocation wrapper for streaming and non-streaming model calls
with timeout classification, bounded retries, exponential backoff with jitter,
finish-reason capture, fallback model execution, and structured-output repair.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from backend.agent.model.client import ModelClient
from backend.agent.model.config import ModelConfig, RetryPolicy
from backend.agent.model.messages import Message, ModelResponse, TokenUsage, TextDeltaCallback
from backend.agent.tools.protocol import ToolSchema

logger = logging.getLogger(__name__)

# Retryable error patterns
_RETRYABLE_PATTERNS = (
    "timeout", "rate limit", "429", "500", "502", "503", "504",
    "connection", "network", "temporary", "overloaded",
)

_NON_RETRYABLE_PATTERNS = (
    "authentication", "invalid_api_key", "permission", "401", "403",
    "invalid_request", "model_not_found", "context_length",
)


@dataclass
class InvocationAttempt:
    """Record of a single model invocation attempt."""
    model_id: str
    attempt_number: int
    success: bool
    is_fallback: bool = False
    error_type: str = ""
    error_message: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_ms: int = 0
    finish_reason: str = ""


@dataclass
class InvocationResult:
    """Result of the invocation policy (may include retries/fallbacks)."""
    response: ModelResponse | None
    attempts: list[InvocationAttempt] = field(default_factory=list)
    final_model_id: str = ""
    success: bool = False
    failure_reason: str = ""

    @property
    def total_retries(self) -> int:
        return max(0, len(self.attempts) - 1)

    @property
    def fallback_used(self) -> bool:
        return any(a.is_fallback for a in self.attempts)

    def to_telemetry(self) -> dict[str, Any]:
        """Emit telemetry without exposing prompts or secrets."""
        return {
            "final_model_id": self.final_model_id,
            "success": self.success,
            "failure_reason": self.failure_reason,
            "total_attempts": len(self.attempts),
            "total_retries": self.total_retries,
            "fallback_used": self.fallback_used,
            "attempts": [
                {
                    "model_id": a.model_id,
                    "attempt_number": a.attempt_number,
                    "success": a.success,
                    "is_fallback": a.is_fallback,
                    "error_type": a.error_type,
                    "input_tokens": a.input_tokens,
                    "output_tokens": a.output_tokens,
                    "elapsed_ms": a.elapsed_ms,
                    "finish_reason": a.finish_reason,
                }
                for a in self.attempts
            ],
        }


def _is_retryable_error(error: Exception) -> bool:
    """Classify whether an error is retryable."""
    # TimeoutError is always retryable
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return True

    error_str = str(error).lower()
    for pattern in _NON_RETRYABLE_PATTERNS:
        if pattern in error_str:
            return False
    for pattern in _RETRYABLE_PATTERNS:
        if pattern in error_str:
            return True
    # Check HTTP status codes in the error
    if hasattr(error, 'status_code'):
        code = getattr(error, 'status_code', 0)
        if code in (429, 500, 502, 503, 504):
            return True
        if code in (401, 403, 404):
            return False
    return False


def _compute_backoff_delay(retry_policy: RetryPolicy, attempt: int) -> float:
    """Compute exponential backoff delay with jitter in seconds."""
    base = retry_policy.base_delay_ms / 1000.0
    delay = min(base * (retry_policy.backoff_factor ** attempt), retry_policy.max_delay_ms / 1000.0)
    jitter = delay * retry_policy.jitter_fraction * (2 * random.random() - 1)
    return max(0.0, delay + jitter)


class InvocationPolicy:
    """Provider-neutral invocation policy for model calls.

    Handles:
    - Unified streaming and non-streaming invocation
    - Timeout classification and bounded retries
    - Exponential backoff with jitter
    - Ordered fallback-model execution
    - Finish-reason capture
    - Attempt telemetry (without exposing prompts or secrets)
    """

    def __init__(
        self,
        primary_client: ModelClient,
        config: ModelConfig,
        fallback_clients: list[ModelClient] | None = None,
    ):
        self._primary = primary_client
        self._config = config
        self._fallbacks = fallback_clients or []
        self._retry_policy = config.retry_policy

    async def invoke(
        self,
        messages: list[Message],
        tool_schemas: list[ToolSchema] | None = None,
        on_text_delta: TextDeltaCallback | None = None,
    ) -> InvocationResult:
        """Invoke the model with retry and fallback policy.

        Returns InvocationResult with the response and attempt telemetry.
        """
        attempts: list[InvocationAttempt] = []

        # Try primary model with retries
        for attempt in range(self._retry_policy.max_retries + 1):
            result = await self._try_invoke(
                client=self._primary,
                model_id=self._config.model,
                messages=messages,
                tool_schemas=tool_schemas,
                on_text_delta=on_text_delta if attempt == 0 else None,  # Only stream on first attempt
                attempt_number=attempt,
                is_fallback=False,
            )
            attempts.append(result.attempt)

            if result.success:
                return InvocationResult(
                    response=result.response,
                    attempts=attempts,
                    final_model_id=self._config.model,
                    success=True,
                )

            # Check if retryable
            if not _is_retryable_error(result.error):
                # Non-retryable, stop immediately
                return InvocationResult(
                    response=None,
                    attempts=attempts,
                    final_model_id=self._config.model,
                    success=False,
                    failure_reason=result.error_type,
                )

            # Wait before retry (except on last attempt)
            if attempt < self._retry_policy.max_retries:
                delay = _compute_backoff_delay(self._retry_policy, attempt)
                logger.info(
                    "Retrying primary model (attempt %d/%d) after %.1fs: %s",
                    attempt + 1, self._retry_policy.max_retries, delay, result.error_type,
                )
                await asyncio.sleep(delay)

        # Primary exhausted, try fallback models
        for fallback_idx, fallback_client in enumerate(self._fallbacks):
            fallback_model = self._config.fallback_models[fallback_idx] if fallback_idx < len(self._config.fallback_models) else f"fallback_{fallback_idx}"

            logger.info("Trying fallback model: %s", fallback_model)

            result = await self._try_invoke(
                client=fallback_client,
                model_id=fallback_model,
                messages=messages,
                tool_schemas=tool_schemas,
                on_text_delta=on_text_delta,
                attempt_number=0,
                is_fallback=True,
            )
            attempts.append(result.attempt)

            if result.success:
                return InvocationResult(
                    response=result.response,
                    attempts=attempts,
                    final_model_id=fallback_model,
                    success=True,
                )

        # All attempts failed
        return InvocationResult(
            response=None,
            attempts=attempts,
            final_model_id=self._config.model,
            success=False,
            failure_reason="all_attempts_exhausted",
        )

    async def _try_invoke(
        self,
        client: ModelClient,
        model_id: str,
        messages: list[Message],
        tool_schemas: list[ToolSchema] | None,
        on_text_delta: TextDeltaCallback | None,
        attempt_number: int,
        is_fallback: bool,
    ) -> _InvokeResult:
        """Try a single invocation. Returns response or error info."""
        start = time.monotonic()
        try:
            if on_text_delta:
                response = await asyncio.wait_for(
                    client.chat_stream(messages, tool_schemas=tool_schemas, on_text_delta=on_text_delta),
                    timeout=self._config.request_timeout_ms / 1000.0,
                )
            else:
                response = await asyncio.wait_for(
                    client.chat(messages, tool_schemas=tool_schemas),
                    timeout=self._config.request_timeout_ms / 1000.0,
                )

            elapsed = int((time.monotonic() - start) * 1000)
            attempt = InvocationAttempt(
                model_id=model_id,
                attempt_number=attempt_number,
                success=True,
                is_fallback=is_fallback,
                input_tokens=response.token_usage.input_tokens if response.token_usage else 0,
                output_tokens=response.token_usage.output_tokens if response.token_usage else 0,
                elapsed_ms=elapsed,
            )
            return _InvokeResult(success=True, response=response, attempt=attempt)

        except asyncio.TimeoutError:
            elapsed = int((time.monotonic() - start) * 1000)
            attempt = InvocationAttempt(
                model_id=model_id,
                attempt_number=attempt_number,
                success=False,
                is_fallback=is_fallback,
                error_type="timeout",
                error_message=f"Request timed out after {self._config.request_timeout_ms}ms",
                elapsed_ms=elapsed,
            )
            return _InvokeResult(success=False, error="timeout", error_type="timeout", attempt=attempt)

        except Exception as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            error_type = type(exc).__name__
            attempt = InvocationAttempt(
                model_id=model_id,
                attempt_number=attempt_number,
                success=False,
                is_fallback=is_fallback,
                error_type=error_type,
                error_message=str(exc)[:500],
                elapsed_ms=elapsed,
            )
            return _InvokeResult(success=False, error=str(exc), error_type=error_type, attempt=attempt)


@dataclass
class _InvokeResult:
    success: bool
    response: ModelResponse | None = None
    error: str = ""
    error_type: str = ""
    attempt: InvocationAttempt = field(default_factory=lambda: InvocationAttempt(model_id="", attempt_number=0, success=False))


def repair_structured_output(
    raw_output: str,
    required_fields: list[str],
) -> dict[str, Any] | None:
    """Attempt to repair malformed structured output.

    Only repairs JSON syntax and schema shape. Does NOT add new evidence
    or candidate ids. Returns None if repair fails or introduces unknown content.
    """
    import re

    # Try direct parse
    try:
        data = json.loads(raw_output)
        if isinstance(data, dict) and all(f in data for f in required_fields):
            return data
    except (json.JSONDecodeError, ValueError):
        pass

    # Try code block extraction
    matches = re.findall(r'```(?:json)?\s*\n?([\s\S]*?)\n?```', raw_output)
    for match in matches:
        match = match.strip()
        try:
            data = json.loads(match)
            if isinstance(data, dict) and all(f in data for f in required_fields):
                return data
        except (json.JSONDecodeError, ValueError):
            pass

    # Try finding { ... } with required fields
    start = raw_output.find("{")
    end = raw_output.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = raw_output[start:end + 1]
        try:
            data = json.loads(candidate)
            if isinstance(data, dict) and all(f in data for f in required_fields):
                return data
        except (json.JSONDecodeError, ValueError):
            pass

    return None
