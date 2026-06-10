from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CompressionConfig:
    """Configuration for context compression.

    Context window and reserves can be overridden by ModelConfig.
    """

    # Master toggle
    context_compression_enabled: bool = True

    # Individual layer toggles
    micro_compact_enabled: bool = True
    auto_compact_enabled: bool = True
    reactive_compact_enabled: bool = True

    # Context window size (tokens) — default for mimo-v2.5-pro is 128K
    # Overridden by ModelConfig.context_window_tokens when available
    context_window_tokens: int = 128_000

    # Buffer for compact summary (tokens)
    compact_max_summary_tokens: int = 4000

    # AutoCompact triggers when estimated tokens exceed this threshold
    auto_compact_buffer_tokens: int = 8000

    # Recent tool results to keep unchanged in MicroCompact
    micro_compact_recent_results: int = 10

    # Minimum character count for MicroCompact to replace a tool result
    micro_compact_min_chars: int = 8000

    # Number of recent messages to preserve in compact summary
    compact_recent_messages: int = 10

    # Maximum retry count for compact summarization
    compact_max_retries: int = 2

    @property
    def auto_compact_threshold(self) -> int:
        """Token threshold for triggering AutoCompact."""
        return (
            self.context_window_tokens
            - self.compact_max_summary_tokens
            - self.auto_compact_buffer_tokens
        )

    @classmethod
    def default(cls) -> CompressionConfig:
        """Create a default configuration."""
        return cls()

    @classmethod
    def disabled(cls) -> CompressionConfig:
        """Create a configuration with all compression disabled."""
        return cls(context_compression_enabled=False)

    @classmethod
    def from_model_config(
        cls,
        context_window_tokens: int,
        output_reserve: int = 4096,
        compaction_reserve: int = 8000,
        **kwargs: object,
    ) -> CompressionConfig:
        """Create compression config derived from model configuration.

        Uses the model's context window and reserves to calculate thresholds.
        """
        return cls(
            context_window_tokens=context_window_tokens,
            compact_max_summary_tokens=int(kwargs.get("compact_max_summary_tokens", 4000)),
            auto_compact_buffer_tokens=int(kwargs.get("auto_compact_buffer_tokens", compaction_reserve)),
            **{k: v for k, v in kwargs.items() if k not in ("compact_max_summary_tokens", "auto_compact_buffer_tokens")},
        )
