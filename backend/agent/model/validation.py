"""Model and runtime configuration validation.

Validates budget reserves, context windows, fallback models, and retry policy.
Emits warning events for inconsistencies and applies conservative defaults.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from backend.agent.model.config import ModelConfig, RetryPolicy

logger = logging.getLogger(__name__)

# Known context window sizes for common models
KNOWN_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-4-32k": 32_768,
    "gpt-3.5-turbo": 16_385,
    "claude-3-opus-20240229": 200_000,
    "claude-3-sonnet-20240229": 200_000,
    "claude-3-haiku-20240307": 200_000,
    "claude-sonnet-4-20250514": 200_000,
    "claude-opus-4-20250514": 200_000,
    "mimo-v2.5-pro": 128_000,
}

# Conservative fallback for unknown models
_DEFAULT_UNKNOWN_CONTEXT_WINDOW = 32_000


@dataclass
class ValidationWarning:
    """A configuration validation warning."""
    field: str
    message: str
    severity: str = "warning"  # warning, error
    applied_default: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "message": self.message,
            "severity": self.severity,
            "applied_default": self.applied_default,
        }


@dataclass
class ValidationResult:
    """Result of configuration validation."""
    valid: bool = True
    warnings: list[ValidationWarning] = field(default_factory=list)
    errors: list[ValidationWarning] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "warnings": [w.to_dict() for w in self.warnings],
            "errors": [e.to_dict() for e in self.errors],
        }

    def add_warning(self, field: str, message: str, applied_default: Any = None) -> None:
        self.warnings.append(ValidationWarning(
            field=field, message=message, applied_default=applied_default,
        ))

    def add_error(self, field: str, message: str) -> None:
        self.valid = False
        self.errors.append(ValidationWarning(
            field=field, message=message, severity="error",
        ))


def resolve_context_window(model: str, configured: int | None = None) -> tuple[int, list[ValidationWarning]]:
    """Resolve context window for a model.

    Returns (context_window_tokens, warnings).
    """
    warnings: list[ValidationWarning] = []

    if configured and configured > 0:
        return configured, warnings

    # Try known model metadata
    for known_model, window in KNOWN_CONTEXT_WINDOWS.items():
        if known_model in model:
            return window, warnings

    # Unknown model with no explicit configuration
    warnings.append(ValidationWarning(
        field="context_window_tokens",
        message=(
            f"Unknown model '{model}' has no configured context window. "
            f"Using conservative default of {_DEFAULT_UNKNOWN_CONTEXT_WINDOW} tokens. "
            f"Set OPENAI_CONTEXT_WINDOW_TOKENS to configure explicitly."
        ),
        applied_default=_DEFAULT_UNKNOWN_CONTEXT_WINDOW,
    ))
    return _DEFAULT_UNKNOWN_CONTEXT_WINDOW, warnings


def validate_model_config(config: ModelConfig) -> ValidationResult:
    """Validate a ModelConfig for consistency and completeness.

    Returns warnings and errors. Does not modify the config.
    """
    result = ValidationResult()

    # Check context window
    if config.context_window_tokens <= 0:
        result.add_error(
            "context_window_tokens",
            f"context_window_tokens must be positive, got {config.context_window_tokens}",
        )

    # Check reserves don't exceed context window
    total_reserve = config.output_reserve_tokens + config.compaction_reserve_tokens
    if total_reserve >= config.context_window_tokens:
        result.add_error(
            "reserves",
            f"Output reserve ({config.output_reserve_tokens}) + compaction reserve "
            f"({config.compaction_reserve_tokens}) = {total_reserve} >= context window "
            f"({config.context_window_tokens}). No room for conversation.",
        )

    # Check output reserve is reasonable
    if config.output_reserve_tokens > config.max_output_tokens * 2:
        result.add_warning(
            "output_reserve_tokens",
            f"Output reserve ({config.output_reserve_tokens}) is more than 2x "
            f"max_output_tokens ({config.max_output_tokens}). This may waste context window.",
        )

    # Check fallback models
    if config.fallback_models:
        for i, model in enumerate(config.fallback_models):
            if not model or not model.strip():
                result.add_warning(
                    "fallback_models",
                    f"Fallback model at index {i} is empty. Ignoring.",
                )

    # Check retry policy
    rp = config.retry_policy
    if rp.max_retries < 0:
        result.add_error(
            "retry_policy.max_retries",
            f"max_retries must be non-negative, got {rp.max_retries}",
        )
    if rp.max_retries > 10:
        result.add_warning(
            "retry_policy.max_retries",
            f"max_retries={rp.max_retries} is unusually high. Consider reducing.",
        )
    if rp.backoff_factor < 1.0:
        result.add_error(
            "retry_policy.backoff_factor",
            f"backoff_factor must be >= 1.0, got {rp.backoff_factor}",
        )
    if rp.jitter_fraction < 0 or rp.jitter_fraction > 1.0:
        result.add_error(
            "retry_policy.jitter_fraction",
            f"jitter_fraction must be in [0, 1], got {rp.jitter_fraction}",
        )

    # Check request timeout
    if config.request_timeout_ms < 1000:
        result.add_warning(
            "request_timeout_ms",
            f"request_timeout_ms={config.request_timeout_ms} is very low. Minimum recommended: 5000ms.",
        )
    if config.request_timeout_ms > 600_000:
        result.add_warning(
            "request_timeout_ms",
            f"request_timeout_ms={config.request_timeout_ms} is very high (>10min).",
        )

    # Check effective context window
    effective = config.effective_context_window
    if effective < 10_000:
        result.add_warning(
            "effective_context_window",
            f"Effective context window ({effective} tokens) is very small. "
            f"Review may be severely limited.",
        )

    return result


def validate_and_apply_defaults(config: ModelConfig) -> tuple[ModelConfig, ValidationResult]:
    """Validate config and apply conservative defaults where needed.

    Returns (possibly_modified_config, validation_result).
    Since ModelConfig is frozen, returns a new instance if defaults are applied.
    """
    result = validate_model_config(config)

    # Apply defaults for unknown context window
    ctx, ctx_warnings = resolve_context_window(config.model, config.context_window_tokens)
    result.warnings.extend(ctx_warnings)

    # If context window was resolved differently, create new config
    needs_update = ctx != config.context_window_tokens
    if needs_update:
        config = ModelConfig(
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
            max_output_tokens=config.max_output_tokens,
            temperature=config.temperature,
            context_window_tokens=ctx,
            output_reserve_tokens=config.output_reserve_tokens,
            compaction_reserve_tokens=config.compaction_reserve_tokens,
            fallback_models=config.fallback_models,
            retry_policy=config.retry_policy,
            request_timeout_ms=config.request_timeout_ms,
            max_repair_attempts=config.max_repair_attempts,
        )

    # Log warnings
    for w in result.warnings:
        logger.warning("Config validation: %s — %s", w.field, w.message)

    for e in result.errors:
        logger.error("Config validation: %s — %s", e.field, e.message)

    return config, result
