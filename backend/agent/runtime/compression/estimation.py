from __future__ import annotations

from backend.agent.model.messages import Message, ToolResultBlock, ToolUseBlock


# Conservative chars-per-token estimate for English text
CHARS_PER_TOKEN = 4

# Overhead per message (role, separators, etc.)
MESSAGE_OVERHEAD_TOKENS = 4

# Overhead per tool block
TOOL_BLOCK_OVERHEAD_TOKENS = 8


def estimate_tokens(text: str) -> int:
    """Estimate token count for a text string.

    Uses a conservative estimate of ~4 chars per token.
    """
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


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
