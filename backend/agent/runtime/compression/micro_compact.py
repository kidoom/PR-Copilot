from __future__ import annotations

import copy
from typing import Any

from backend.agent.model.messages import Message, Role, ToolResultBlock, ToolUseBlock


# Whitelist of tools whose results can be compacted
COMPACTABLE_TOOLS = frozenset({
    "read_file_patch",
    "search_repo",
    "search_tests_for",
    "read_repo_manifest",
    "read_check_summary",
    "search_diff",
})

# Tools that are critical evidence sources — never compact their results
EVIDENCE_CRITICAL_TOOLS = frozenset({
    "read_repo_file",
})

# Tools that should never be compacted
EXCLUDED_TOOLS = frozenset({
    "finish_context_package",
})


def deep_copy_messages(messages: list[Message]) -> list[Message]:
    """Create a deep copy of messages for MicroCompact.

    Never mutates the original messages.
    """
    return copy.deepcopy(messages)


def is_compactable_tool_use(block: ToolUseBlock) -> bool:
    """Check if a tool use block is from a compactable tool."""
    return block.name in COMPACTABLE_TOOLS


def is_compactable_tool_result(
    block: ToolResultBlock,
    tool_use_name: str,
    is_error: bool,
    min_chars: int,
) -> bool:
    """Check if a tool result can be compacted.

    Conditions:
    - Tool is in compactable whitelist
    - Tool is not evidence-critical (never compacted)
    - Result is not an error
    - Result content is above minimum size threshold
    """
    if tool_use_name in EXCLUDED_TOOLS:
        return False
    if tool_use_name in EVIDENCE_CRITICAL_TOOLS:
        return False
    if tool_use_name not in COMPACTABLE_TOOLS:
        return False
    if is_error:
        return False
    if len(block.content) < min_chars:
        return False
    return True


def build_placeholder(tool_name: str, original_chars: int, tool_input: dict[str, Any] | None = None) -> str:
    """Build a compact placeholder for a tool result.

    Includes tool name, original size, and available input metadata.
    """
    parts = [
        f"[Compacted: {tool_name}]",
        f"Original size: {original_chars:,} chars",
    ]

    if tool_input:
        # Include relevant input metadata
        if "path" in tool_input:
            parts.append(f"File: {tool_input['path']}")
        if "query" in tool_input:
            parts.append(f"Query: {tool_input['query']}")
        if "filename" in tool_input:
            parts.append(f"File: {tool_input['filename']}")

    return " | ".join(parts)


def micro_compact_messages(
    messages: list[Message],
    recent_count: int = 3,
    min_chars: int = 1000,
) -> list[Message]:
    """Create a compacted copy of messages for model request.

    Replaces only old large successful compactable tool results with placeholders.
    Never mutates the original messages.

    Args:
        messages: Original messages to compact.
        recent_count: Number of recent tool results to keep unchanged.
        min_chars: Minimum character count to consider for compaction.

    Returns:
        Deep copy with compacted tool results (originals unchanged).
    """
    if not messages:
        return []

    # Deep copy to avoid mutation
    compacted = deep_copy_messages(messages)

    # Track tool results from end to identify recent ones
    tool_result_indices: list[tuple[int, int]] = []  # (msg_idx, block_idx)

    for i, msg in enumerate(compacted):
        if msg.role == Role.TOOL and isinstance(msg.content, list):
            for j, block in enumerate(msg.content):
                if isinstance(block, ToolResultBlock):
                    tool_result_indices.append((i, j))

    # Keep the last N tool results unchanged
    recent_result_ids: set[str] = set()
    if recent_count > 0:
        for _, (msg_idx, block_idx) in enumerate(tool_result_indices[-recent_count:]):
            block = compacted[msg_idx].content[block_idx]
            if isinstance(block, ToolResultBlock):
                recent_result_ids.add(block.tool_use_id)

    # Build a map of tool_use_id -> tool_name and tool_input
    tool_info: dict[str, tuple[str, dict[str, Any]]] = {}
    for msg in compacted:
        if msg.role == Role.ASSISTANT and isinstance(msg.content, list):
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    tool_info[block.tool_use_id] = (block.name, block.input)

    # Compact eligible tool results
    for msg_idx, block_idx in tool_result_indices:
        msg = compacted[msg_idx]
        if not isinstance(msg.content, list):
            continue

        block = msg.content[block_idx]
        if not isinstance(block, ToolResultBlock):
            continue

        # Skip recent results
        if block.tool_use_id in recent_result_ids:
            continue

        # Get tool info
        tool_name, tool_input = tool_info.get(block.tool_use_id, ("unknown", {}))

        # Check if compactable
        is_error = block.is_error
        if not is_compactable_tool_result(block, tool_name, is_error, min_chars):
            continue

        # Replace with placeholder
        placeholder = build_placeholder(tool_name, len(block.content), tool_input)
        msg.content[block_idx] = ToolResultBlock(
            tool_use_id=block.tool_use_id,
            content=placeholder,
            is_error=False,
        )

    return compacted
