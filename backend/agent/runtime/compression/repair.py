from __future__ import annotations

from backend.agent.model.messages import Message, Role, ToolResultBlock, ToolUseBlock


def repair_tool_message_pairs(messages: list[Message]) -> list[Message]:
    """Repair tool-use and tool-result pairs in a message list.

    Ensures:
    - Every tool_result has a matching tool_use (remove orphan results)
    - Every tool_use has a matching tool_result (add placeholder or remove)

    Args:
        messages: List of messages to repair.

    Returns:
        Repaired list of messages safe for model calls.
    """
    if not messages:
        return messages

    # Collect tool_use_ids from assistant messages
    tool_use_ids: set[str] = set()
    # Collect tool_result references
    tool_result_refs: set[str] = set()

    for msg in messages:
        if msg.role == Role.ASSISTANT and isinstance(msg.content, list):
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    tool_use_ids.add(block.tool_use_id)
        elif msg.role == Role.TOOL and isinstance(msg.content, list):
            for block in msg.content:
                if isinstance(block, ToolResultBlock):
                    tool_result_refs.add(block.tool_use_id)

    # Find orphans
    orphan_results = tool_result_refs - tool_use_ids
    orphan_uses = tool_use_ids - tool_result_refs

    # If no orphans, return as-is
    if not orphan_results and not orphan_uses:
        return messages

    # Repair messages
    repaired: list[Message] = []

    for msg in messages:
        if msg.role == Role.TOOL and isinstance(msg.content, list):
            # Filter out orphan tool results
            valid_blocks = [
                b for b in msg.content
                if not isinstance(b, ToolResultBlock) or b.tool_use_id not in orphan_results
            ]
            if valid_blocks:
                repaired.append(Message(role=Role.TOOL, content=valid_blocks))
            # Skip empty tool result messages

        elif msg.role == Role.ASSISTANT and isinstance(msg.content, list):
            # Check if this message has orphan tool uses
            has_orphan = any(
                isinstance(b, ToolUseBlock) and b.tool_use_id in orphan_uses
                for b in msg.content
            )

            if has_orphan:
                # Keep text content, remove orphan tool uses
                valid_blocks = []
                text_content = ""
                for b in msg.content:
                    if isinstance(b, ToolUseBlock):
                        if b.tool_use_id not in orphan_uses:
                            valid_blocks.append(b)
                    elif isinstance(b, str):
                        text_content += b

                if valid_blocks:
                    # Still has valid tool uses
                    repaired.append(Message(role=Role.ASSISTANT, content=valid_blocks))
                elif text_content:
                    # Only text remains
                    repaired.append(Message(role=Role.ASSISTANT, content=text_content))
                # Skip if nothing valid remains
            else:
                repaired.append(msg)

        else:
            repaired.append(msg)

    # Add placeholder tool results for orphan tool uses that we kept
    # (We removed them above, so we don't need placeholders)

    return repaired
