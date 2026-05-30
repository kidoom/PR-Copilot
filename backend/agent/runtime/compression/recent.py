from __future__ import annotations

from backend.agent.model.messages import Message
from backend.agent.runtime.compression.repair import repair_tool_message_pairs


def select_recent_messages(
    messages: list[Message],
    count: int,
) -> list[Message]:
    """Select recent messages with tool-pair-safe repair.

    Takes the last N messages and repairs any broken tool pairs.

    Args:
        messages: Full list of messages.
        count: Number of recent messages to select.

    Returns:
        Repaired list of recent messages.
    """
    if not messages or count <= 0:
        return []

    # Take last N messages
    recent = messages[-count:]

    # Repair tool pairs in the selection
    return repair_tool_message_pairs(recent)
