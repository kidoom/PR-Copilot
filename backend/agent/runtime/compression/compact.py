from __future__ import annotations

import json
from typing import Any

from backend.agent.model.client import ModelClient
from backend.agent.model.messages import Message, Role, ToolResultBlock, ToolUseBlock
from backend.agent.runtime.compression.compact_prompts import (
    CompactProfile,
    compact_user_prompt,
    get_compact_profile_prompt,
)
from backend.agent.runtime.compression.config import CompressionConfig
from backend.agent.runtime.compression.estimation import estimate_tokens
from backend.agent.runtime.compression.recent import select_recent_messages
from backend.agent.runtime.compression.repair import repair_tool_message_pairs
from backend.agent.runtime.memory.store import FileMemoryStore


def serialize_messages_for_compact(messages: list[Message]) -> str:
    """Serialize messages to plain text for compact summarization.

    Handles plain text, assistant tool-use blocks, and tool-result blocks.
    """
    parts = []

    for i, msg in enumerate(messages):
        role = msg.role.value.upper()
        content = msg.content

        if isinstance(content, str):
            parts.append(f"[{role}] {content}")
        elif isinstance(content, list):
            block_texts = []
            for block in content:
                if isinstance(block, ToolUseBlock):
                    # Serialize tool use
                    input_str = json.dumps(block.input, ensure_ascii=False)[:500]
                    block_texts.append(f"[Tool Call: {block.name}] input={input_str}")
                elif isinstance(block, ToolResultBlock):
                    # Serialize tool result (may be compacted placeholder)
                    result_text = block.content[:500] if len(block.content) > 500 else block.content
                    if block.is_error:
                        block_texts.append(f"[Tool Error] {result_text}")
                    else:
                        block_texts.append(f"[Tool Result] {result_text}")
            parts.append(f"[{role}] {' '.join(block_texts)}")
        else:
            parts.append(f"[{role}] {str(content)}")

    return "\n\n".join(parts)


def is_context_length_error(error: Exception) -> bool:
    """Detect provider-neutral context-length errors.

    Checks error type name and message for known patterns.
    """
    error_type = type(error).__name__.lower()
    error_msg = str(error).lower()

    # Check error type name
    context_length_types = [
        "contextlength",
        "context_length",
        "contextwindow",
        "context_window",
        "maxtoken",
        "max_token",
        "tokenlimit",
        "token_limit",
    ]
    for pattern in context_length_types:
        if pattern in error_type:
            return True

    # Check error message
    context_length_messages = [
        "context length",
        "context window",
        "maximum context",
        "token limit",
        "too many tokens",
        "maximum tokens",
        "context_length_exceeded",
    ]
    for pattern in context_length_messages:
        if pattern in error_msg:
            return True

    return False


def build_summary_boundary_message(summary_text: str) -> Message:
    """Build a summary boundary message for runtime rewrite."""
    return Message(
        role=Role.USER,
        content=f"[Context Summary]\n{summary_text}",
    )


def snip_large_tool_result(content: str, max_chars: int = 2000) -> str:
    """Snip oversized tool result content for lightweight serialization."""
    if len(content) <= max_chars:
        return content
    return content[:max_chars] + f"... [snipped, {len(content)} total chars]"


def serialize_recent_messages_lightweight(messages: list[Message]) -> list[dict[str, Any]]:
    """Serialize recent messages for summary payload with snipped tool results."""
    serialized = []

    for msg in messages:
        content = msg.content

        if isinstance(content, str):
            serialized.append({"role": msg.role.value, "content": content})
        elif isinstance(content, list):
            blocks = []
            for block in content:
                if isinstance(block, ToolUseBlock):
                    blocks.append({
                        "tool_use_id": block.tool_use_id,
                        "name": block.name,
                        "input": block.input,
                    })
                elif isinstance(block, ToolResultBlock):
                    # Snip large tool results
                    snipped_content = snip_large_tool_result(block.content)
                    blocks.append({
                        "tool_use_id": block.tool_use_id,
                        "content": snipped_content,
                        "is_error": block.is_error,
                    })
            serialized.append({"role": msg.role.value, "content": blocks})
        else:
            serialized.append({"role": msg.role.value, "content": str(content)})

    return serialized


async def execute_compact(
    *,
    model: ModelClient,
    messages: list[Message],
    profile: CompactProfile,
    config: CompressionConfig,
    memory_store: FileMemoryStore | None = None,
    session_id: str = "",
) -> tuple[str, list[Message]] | None:
    """Execute context compaction.

    Returns:
        Tuple of (summary_text, recent_messages) if successful, None if failed.
    """
    if not messages:
        return None

    # Build compact prompts
    system_prompt = get_compact_profile_prompt(profile)
    user_prompt = compact_user_prompt(len(messages), profile)

    # Serialize messages for compact
    serialized = serialize_messages_for_compact(messages)

    # Build compact request messages
    compact_messages = [
        Message(role=Role.SYSTEM, content=system_prompt),
        Message(role=Role.USER, content=f"{user_prompt}\n\n---\n\n{serialized}"),
    ]

    # Estimate compact request tokens
    compact_tokens = estimate_tokens(system_prompt) + estimate_tokens(user_prompt) + estimate_tokens(serialized)

    # Retry loop for too-large requests
    retry_count = 0
    max_retries = config.compact_max_retries

    while retry_count <= max_retries:
        try:
            # Call model without tools
            response = await model.chat(compact_messages, tool_schemas=[])

            if not response.content:
                # Empty summary is a failure
                return None

            summary_text = response.content

            # Select recent messages to preserve
            recent_messages = select_recent_messages(
                messages, config.compact_recent_messages
            )

            return summary_text, recent_messages

        except Exception as e:
            if is_context_length_error(e) and retry_count < max_retries:
                # Reduce content and retry
                retry_count += 1

                # Remove older messages from serialized content
                half = len(messages) // 2
                messages = messages[half:]
                serialized = serialize_messages_for_compact(messages)
                user_prompt = compact_user_prompt(len(messages), profile)

                compact_messages = [
                    Message(role=Role.SYSTEM, content=system_prompt),
                    Message(role=Role.USER, content=f"{user_prompt}\n\n---\n\n{serialized}"),
                ]
                continue
            else:
                # Non-context-length error or max retries exceeded
                return None

    return None
