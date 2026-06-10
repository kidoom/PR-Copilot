"""Shared validated tool executor for the ReAct loop.

Centralizes tool resolution, argument validation, permission checks,
timeouts, observation truncation, and budget charging. All model-proposed
tool calls pass through ToolExecutor before invoking tool business logic.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from backend.agent.model.messages import ToolUseBlock, ToolResultBlock
from backend.agent.runtime.accounting import TaskAccounting, RuntimeFailureReason
from backend.agent.tools.protocol import Tool, ToolConsentFn
from backend.agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Default per-tool timeout in seconds
_DEFAULT_TOOL_TIMEOUT_S = 60

# Default maximum observation tokens (approximate chars * 4)
_DEFAULT_MAX_OBSERVATION_TOKENS = 8000

# Observation char limit (conservative)
_MAX_OBSERVATION_CHARS = 32000


@dataclass(frozen=True)
class ToolObservation:
    """A validated, budget-charged tool observation ready for model delivery."""
    tool_use_id: str
    content: str
    is_error: bool = False
    error_kind: str = ""  # invalid_json, schema_validation, unknown_tool, denied, timeout, cancelled, exception, budget_exhausted
    observation_tokens: int = 0
    elapsed_ms: int = 0
    tool_name: str = ""

    def to_result_block(self) -> ToolResultBlock:
        return ToolResultBlock(
            tool_use_id=self.tool_use_id,
            content=self.content,
            is_error=self.is_error,
        )


@dataclass
class ToolExecutorConfig:
    """Configuration for the shared tool executor."""
    tool_timeout_s: int = _DEFAULT_TOOL_TIMEOUT_S
    max_observation_tokens: int = _DEFAULT_MAX_OBSERVATION_TOKENS
    max_observation_chars: int = _MAX_OBSERVATION_CHARS
    allowed_tools: set[str] | None = None  # None means all registered tools
    enable_schema_validation: bool = True


class ToolExecutor:
    """Shared validated executor for all model-proposed tool calls.

    Lifecycle:
    1. Resolve the tool from the registry
    2. Validate arguments against declared JSON Schema
    3. Normalize bounded integer and string inputs
    4. Enforce permission and consent policy
    5. Execute under a per-tool timeout
    6. Convert denial, timeout, cancellation, and exceptions into structured observations
    7. Truncate or reject oversized observations
    8. Charge task budgets using the final model-visible observation
    """

    def __init__(
        self,
        registry: ToolRegistry,
        config: ToolExecutorConfig | None = None,
        consent_fn: ToolConsentFn | None = None,
        task_accounting: TaskAccounting | None = None,
        on_tool_call: Callable[[dict[str, Any]], None] | None = None,
        on_tool_result: Callable[[dict[str, Any]], None] | None = None,
        agent_kind: str = "",
        agent_type: str = "",
        task_id: str = "",
        child_session_id: str = "",
    ):
        self._registry = registry
        self._config = config or ToolExecutorConfig()
        self._consent_fn = consent_fn
        self._task_accounting = task_accounting
        self._on_tool_call = on_tool_call
        self._on_tool_result = on_tool_result
        self._agent_kind = agent_kind
        self._agent_type = agent_type
        self._task_id = task_id
        self._child_session_id = child_session_id

    async def execute(self, block: ToolUseBlock) -> ToolObservation:
        """Execute a single tool call through the full validation pipeline.

        Returns a ToolObservation ready for model delivery.
        """
        start_time = time.monotonic()

        # Emit tool call event
        if self._on_tool_call:
            self._on_tool_call({
                "agent_kind": self._agent_kind,
                "agent_type": self._agent_type,
                "task_id": self._task_id,
                "child_session_id": self._child_session_id,
                "tool_name": block.name,
                "tool_use_id": block.tool_use_id,
                "input": block.input,
            })

        # 1. Resolve tool
        tool = self._registry.resolve(block.name)
        if tool is None:
            elapsed = int((time.monotonic() - start_time) * 1000)
            obs = self._make_observation(
                tool_use_id=block.tool_use_id,
                content=f"Unknown tool: {block.name}",
                is_error=True,
                error_kind="unknown_tool",
                elapsed_ms=elapsed,
                tool_name=block.name,
            )
            self._emit_result_event(obs)
            return obs

        # 2. Check allowlist
        if self._config.allowed_tools is not None:
            if block.name not in self._config.allowed_tools:
                elapsed = int((time.monotonic() - start_time) * 1000)
                obs = self._make_observation(
                    tool_use_id=block.tool_use_id,
                    content=f"Tool '{block.name}' is not in the allowed tools list.",
                    is_error=True,
                    error_kind="denied",
                    elapsed_ms=elapsed,
                    tool_name=block.name,
                )
                self._emit_result_event(obs)
                return obs

        # 3. Validate arguments
        validated_input = self._validate_arguments(block)
        if validated_input is None:
            elapsed = int((time.monotonic() - start_time) * 1000)
            obs = self._make_observation(
                tool_use_id=block.tool_use_id,
                content=f"Invalid tool arguments for '{block.name}': failed schema validation.",
                is_error=True,
                error_kind="schema_validation",
                elapsed_ms=elapsed,
                tool_name=block.name,
            )
            self._emit_result_event(obs)
            return obs

        # 4. Check consent
        if tool.requires_consent and self._consent_fn is not None:
            approved = await self._consent_fn(tool, validated_input)
            if not approved:
                elapsed = int((time.monotonic() - start_time) * 1000)
                obs = self._make_observation(
                    tool_use_id=block.tool_use_id,
                    content=f"Tool '{block.name}' requires user consent and was denied.",
                    is_error=True,
                    error_kind="denied",
                    elapsed_ms=elapsed,
                    tool_name=block.name,
                )
                self._emit_result_event(obs)
                return obs

        # 5. Execute with timeout
        try:
            result = await asyncio.wait_for(
                tool.call(validated_input),
                timeout=self._config.tool_timeout_s,
            )
            is_error = False
            error_kind = ""
        except asyncio.TimeoutError:
            elapsed = int((time.monotonic() - start_time) * 1000)
            obs = self._make_observation(
                tool_use_id=block.tool_use_id,
                content=f"Tool '{block.name}' timed out after {self._config.tool_timeout_s}s.",
                is_error=True,
                error_kind="timeout",
                elapsed_ms=elapsed,
                tool_name=block.name,
            )
            self._emit_result_event(obs)
            self._charge_budget(obs)
            return obs
        except asyncio.CancelledError:
            elapsed = int((time.monotonic() - start_time) * 1000)
            obs = self._make_observation(
                tool_use_id=block.tool_use_id,
                content=f"Tool '{block.name}' was cancelled.",
                is_error=True,
                error_kind="cancelled",
                elapsed_ms=elapsed,
                tool_name=block.name,
            )
            self._emit_result_event(obs)
            self._charge_budget(obs)
            return obs
        except Exception as exc:
            elapsed = int((time.monotonic() - start_time) * 1000)
            obs = self._make_observation(
                tool_use_id=block.tool_use_id,
                content=f"Tool '{block.name}' error: {str(exc)[:2000]}",
                is_error=True,
                error_kind="exception",
                elapsed_ms=elapsed,
                tool_name=block.name,
            )
            self._emit_result_event(obs)
            self._charge_budget(obs)
            return obs

        elapsed = int((time.monotonic() - start_time) * 1000)

        # 6. Truncate oversized observations
        content = result if isinstance(result, str) else str(result)
        content, was_truncated = self._truncate_observation(content)

        obs = self._make_observation(
            tool_use_id=block.tool_use_id,
            content=content,
            is_error=is_error,
            error_kind=error_kind,
            elapsed_ms=elapsed,
            tool_name=block.name,
        )

        # 7. Emit and charge
        self._emit_result_event(obs)
        self._charge_budget(obs)
        return obs

    def _validate_arguments(self, block: ToolUseBlock) -> dict[str, Any] | None:
        """Validate tool arguments against declared JSON Schema.

        Returns validated input dict, or None if validation fails.
        Does NOT silently convert malformed args to {}.
        """
        tool = self._registry.resolve(block.name)
        if tool is None:
            return None

        # Check if input is a dict
        if not isinstance(block.input, dict):
            return None

        # Basic JSON Schema validation if schema is available
        schema = tool.input_schema
        if schema and self._config.enable_schema_validation:
            # Check required fields
            required = schema.get("required", [])
            for field_name in required:
                if field_name not in block.input:
                    return None

            # Check property types (basic validation)
            properties = schema.get("properties", {})
            for prop_name, prop_schema in properties.items():
                if prop_name in block.input:
                    expected_type = prop_schema.get("type")
                    value = block.input[prop_name]
                    if expected_type == "string" and not isinstance(value, str):
                        # Try coercion
                        try:
                            block.input[prop_name] = str(value)
                        except (ValueError, TypeError):
                            return None
                    elif expected_type == "integer":
                        if not isinstance(value, int):
                            try:
                                block.input[prop_name] = int(value)
                            except (ValueError, TypeError):
                                return None
                    elif expected_type == "number":
                        if not isinstance(value, (int, float)):
                            try:
                                block.input[prop_name] = float(value)
                            except (ValueError, TypeError):
                                return None
                    elif expected_type == "boolean":
                        if not isinstance(value, bool):
                            return None

        return block.input

    def _truncate_observation(self, content: str) -> tuple[str, bool]:
        """Truncate oversized observations. Returns (content, was_truncated)."""
        if len(content) <= self._config.max_observation_chars:
            return content, False

        # Truncate with marker
        truncated = content[:self._config.max_observation_chars]
        truncated += f"\n\n[Observation truncated from {len(content)} to {self._config.max_observation_chars} chars]"
        return truncated, True

    def _make_observation(
        self,
        tool_use_id: str,
        content: str,
        is_error: bool,
        error_kind: str,
        elapsed_ms: int,
        tool_name: str,
    ) -> ToolObservation:
        """Create a ToolObservation with estimated token count."""
        # Rough estimate: 1 token per 4 chars
        obs_tokens = max(1, len(content) // 4)
        return ToolObservation(
            tool_use_id=tool_use_id,
            content=content,
            is_error=is_error,
            error_kind=error_kind,
            observation_tokens=obs_tokens,
            elapsed_ms=elapsed_ms,
            tool_name=tool_name,
        )

    def _emit_result_event(self, obs: ToolObservation) -> None:
        """Emit tool result event to callbacks."""
        if self._on_tool_result:
            self._on_tool_result({
                "agent_kind": self._agent_kind,
                "agent_type": self._agent_type,
                "task_id": self._task_id,
                "child_session_id": self._child_session_id,
                "tool_name": obs.tool_name,
                "tool_use_id": obs.tool_use_id,
                "output": obs.content,
                "is_error": obs.is_error,
                "error_kind": obs.error_kind,
                "observation_tokens": obs.observation_tokens,
                "elapsed_ms": obs.elapsed_ms,
            })

    def _charge_budget(self, obs: ToolObservation) -> None:
        """Charge observation to task accounting."""
        if self._task_accounting:
            self._task_accounting.observation_tokens += obs.observation_tokens
            self._task_accounting.elapsed_ms += obs.elapsed_ms
            if obs.tool_name in ("search_repo", "search_diff", "search_tests_for"):
                self._task_accounting.search_count += 1
            elif obs.tool_name in ("read_file_patch", "read_repo_file", "read_repo_manifest", "read_check_summary"):
                self._task_accounting.file_read_count += 1
