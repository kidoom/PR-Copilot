from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CompressionConfig:
    """Configuration for context compression."""

    # Master toggle
    context_compression_enabled: bool = True

    # Individual layer toggles
    micro_compact_enabled: bool = True
    auto_compact_enabled: bool = True
    reactive_compact_enabled: bool = True

    # Context window size (tokens)
    context_window_tokens: int = 128000

    # Buffer for compact summary (tokens)
    compact_max_summary_tokens: int = 4000

    # AutoCompact triggers when estimated tokens exceed this threshold
    auto_compact_buffer_tokens: int = 8000

    # Recent tool results to keep unchanged in MicroCompact
    micro_compact_recent_results: int = 3

    # Minimum character count for MicroCompact to replace a tool result
    micro_compact_min_chars: int = 1000

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
