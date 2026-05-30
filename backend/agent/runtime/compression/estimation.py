from __future__ import annotations

import re

from backend.agent.model.messages import Message, ToolResultBlock, ToolUseBlock


# Conservative chars-per-token estimate for English text
CHARS_PER_TOKEN = 4

# CJK characters typically use ~1 token per character
CJK_CHARS_PER_TOKEN = 1

# Overhead per message (role, separators, etc.)
MESSAGE_OVERHEAD_TOKENS = 4

# Overhead per tool block
TOOL_BLOCK_OVERHEAD_TOKENS = 8

# CJK character range pattern
CJK_PATTERN = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u2e80-\u2eff\u3000-\u303f\uff00-\uffef]')


def _count_cjk_chars(text: str) -> int:
    """Count CJK characters in text."""
    return len(CJK_PATTERN.findall(text))


def estimate_tokens(text: str) -> int:
    """Estimate token count for a text string.

    Uses conservative estimates:
    - English/code: ~4 chars per token
    - CJK characters: ~1 token per character (more conservative)

    The estimate takes the maximum of English-based and CJK-based calculations.
    """
    if not text:
        return 0

    # English-based estimate
    english_estimate = len(text) // CHARS_PER_TOKEN

    # CJK-based estimate
    cjk_count = _count_cjk_chars(text)
    non_cjk_len = len(text) - cjk_count
    cjk_estimate = cjk_count + (non_cjk_len // CHARS_PER_TOKEN)

    # Return the more conservative (higher) estimate
    return max(1, max(english_estimate, cjk_estimate))


def estimate_message_tokens(message: Message) -> int:
    """Estimate token count for a single Message.

    Includes message overhead and structured content.
    """
    total = MESSAGE_OVERHEAD_TOKENS

    content = message.content
    if isinstance(content, str):
        total += estimate_tokens(content)
    elif isinstance(content, list):
        for block in content:
            total += _estimate_block_tokens(block)

    return total


def _estimate_block_tokens(block: ToolUseBlock | ToolResultBlock) -> int:
    """Estimate tokens for a tool block."""
    total = TOOL_BLOCK_OVERHEAD_TOKENS

    if isinstance(block, ToolUseBlock):
        # Tool name
        total += estimate_tokens(block.name)
        # Tool input (serialized JSON)
        import json
        input_str = json.dumps(block.input, ensure_ascii=False)
        total += estimate_tokens(input_str)
    elif isinstance(block, ToolResultBlock):
        # Tool result content
        total += estimate_tokens(block.content)

    return total


def estimate_messages_tokens(messages: list[Message]) -> int:
    """Estimate total token count for a list of messages."""
    return sum(estimate_message_tokens(msg) for msg in messages)
