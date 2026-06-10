from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Conservative defaults for unknown models
_DEFAULT_CONTEXT_WINDOW = 128_000
_DEFAULT_OUTPUT_RESERVE = 4_096
_DEFAULT_COMPACTION_RESERVE = 8_000


@dataclass(frozen=True)
class RetryPolicy:
    """Retry configuration for model invocations."""
    max_retries: int = 3
    base_delay_ms: int = 1000
    max_delay_ms: int = 30000
    backoff_factor: float = 2.0
    jitter_fraction: float = 0.25

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_retries": self.max_retries,
            "base_delay_ms": self.base_delay_ms,
            "max_delay_ms": self.max_delay_ms,
            "backoff_factor": self.backoff_factor,
            "jitter_fraction": self.jitter_fraction,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RetryPolicy:
        return cls(
            max_retries=data.get("max_retries", 3),
            base_delay_ms=data.get("base_delay_ms", 1000),
            max_delay_ms=data.get("max_delay_ms", 30000),
            backoff_factor=data.get("backoff_factor", 2.0),
            jitter_fraction=data.get("jitter_fraction", 0.25),
        )


@dataclass(frozen=True)
class ModelConfig:
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o"
    max_output_tokens: int = 4096
    temperature: float = 0.0

    # Context window and reserves (new fields with safe defaults)
    context_window_tokens: int = _DEFAULT_CONTEXT_WINDOW
    output_reserve_tokens: int = _DEFAULT_OUTPUT_RESERVE
    compaction_reserve_tokens: int = _DEFAULT_COMPACTION_RESERVE

    # Fallback models in priority order
    fallback_models: tuple[str, ...] = ()

    # Retry policy
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)

    # Request timeout in milliseconds
    request_timeout_ms: int = 120_000

    # Structured output repair
    max_repair_attempts: int = 1

    @classmethod
    def from_env(cls, prefix: str = "OPENAI") -> ModelConfig:
        api_key = os.environ.get(f"{prefix}_API_KEY", "")
        base_url = os.environ.get(f"{prefix}_BASE_URL", "https://api.openai.com/v1")
        model = os.environ.get(f"{prefix}_MODEL", "gpt-4o")
        max_output_tokens = int(os.environ.get(f"{prefix}_MAX_OUTPUT_TOKENS", "4096"))
        temperature = float(os.environ.get(f"{prefix}_TEMPERATURE", "0.0"))

        # New configuration from environment
        context_window_tokens = int(
            os.environ.get(f"{prefix}_CONTEXT_WINDOW_TOKENS", str(_DEFAULT_CONTEXT_WINDOW))
        )
        output_reserve_tokens = int(
            os.environ.get(f"{prefix}_OUTPUT_RESERVE_TOKENS", str(_DEFAULT_OUTPUT_RESERVE))
        )
        compaction_reserve_tokens = int(
            os.environ.get(f"{prefix}_COMPACTION_RESERVE_TOKENS", str(_DEFAULT_COMPACTION_RESERVE))
        )
        request_timeout_ms = int(os.environ.get(f"{prefix}_REQUEST_TIMEOUT_MS", "120000"))
        max_repair_attempts = int(os.environ.get(f"{prefix}_MAX_REPAIR_ATTEMPTS", "1"))

        # Fallback models (comma-separated)
        fallback_str = os.environ.get(f"{prefix}_FALLBACK_MODELS", "")
        fallback_models = tuple(
            m.strip() for m in fallback_str.split(",") if m.strip()
        ) if fallback_str else ()

        # Retry policy from environment
        retry_policy = RetryPolicy(
            max_retries=int(os.environ.get(f"{prefix}_MAX_RETRIES", "3")),
            base_delay_ms=int(os.environ.get(f"{prefix}_RETRY_BASE_DELAY_MS", "1000")),
            max_delay_ms=int(os.environ.get(f"{prefix}_RETRY_MAX_DELAY_MS", "30000")),
            backoff_factor=float(os.environ.get(f"{prefix}_RETRY_BACKOFF_FACTOR", "2.0")),
            jitter_fraction=float(os.environ.get(f"{prefix}_RETRY_JITTER_FRACTION", "0.25")),
        )

        return cls(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            model=model,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            context_window_tokens=context_window_tokens,
            output_reserve_tokens=output_reserve_tokens,
            compaction_reserve_tokens=compaction_reserve_tokens,
            fallback_models=fallback_models,
            retry_policy=retry_policy,
            request_timeout_ms=request_timeout_ms,
            max_repair_attempts=max_repair_attempts,
        )

    @property
    def effective_context_window(self) -> int:
        """Effective context window after reserves."""
        return max(
            10_000,
            self.context_window_tokens - self.output_reserve_tokens - self.compaction_reserve_tokens,
        )
