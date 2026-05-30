from __future__ import annotations

from typing import Any

from backend.agent.model.messages import Message, Role, ToolResultBlock, ToolUseBlock
from backend.agent.runtime.memory.models import EntryType, TranscriptEntry
from backend.agent.runtime.memory.store import FileMemoryStore, SessionNotFoundError


def hydrate_messages(
    store: FileMemoryStore,
    session_id: str,
) -> list[Message]:
    """Hydrate model messages from a session's transcript.

    Only message entries and summary boundary messages are included.
    Agent events, session metadata, todo state, and evidence packages are ignored.

    Args:
        store: The memory store.
        session_id: Session to hydrate.

    Returns:
        List of model messages suitable for model context.

    Raises:
        SessionNotFoundError: If session not found.
    """
    entries = store.load_entries(session_id)
    if not entries:
        return []

    # Convert entries to messages
    messages: list[Message] = []
    for entry in entries:
        if entry.type == EntryType.MESSAGE:
            msg = _entry_to_message(entry)
            if msg:
                messages.append(msg)
        elif entry.type == EntryType.SUMMARY:
            # Apply summary boundary - replaces older messages
            boundary_messages = _apply_summary_boundary(entry, messages)
            messages = boundary_messages
        # Ignore other entry types (agent_event, session_meta, todo_state, evidence_package)

    # Repair tool pairs
    messages = _repair_tool_pairs(messages)

    return messages


def _entry_to_message(entry: TranscriptEntry) -> Message | None:
    """Convert a message transcript entry to a model Message."""
    payload = entry.payload
    if not payload:
        return None

    role_str = payload.get("role", "")
    try:
        role = Role(role_str)
    except ValueError:
        return None

    content = payload.get("content", "")

    # Handle structured content (tool_use/tool_result blocks)
    if isinstance(content, list):
        blocks = []
        for block in content:
            if isinstance(block, dict):
                if "tool_use_id" in block and "name" in block:
                    # ToolUseBlock
                    blocks.append(
                        ToolUseBlock(
                            tool_use_id=block["tool_use_id"],
                            name=block["name"],
                            input=block.get("input", {}),
                        )
                    )
                elif "tool_use_id" in block:
                    # ToolResultBlock
                    blocks.append(
                        ToolResultBlock(
                            tool_use_id=block["tool_use_id"],
                            content=block.get("content", ""),
                            is_error=block.get("is_error", False),
                        )
                    )
        content = blocks if blocks else str(content)

    return Message(role=role, content=content)


def _apply_summary_boundary(
    entry: TranscriptEntry,
    previous_messages: list[Message],
) -> list[Message]:
    """Apply a summary boundary, replacing older messages.

    Supports standardized summary payload:
    - reason: Why compaction happened (e.g., "auto_compact", "reactive_compact")
    - summary: The summary text (also accepts legacy "summary_text")
    - before_message_count: Messages before compaction
    - after_message_count: Messages after compaction
    - recent_messages: Recent messages to preserve

    Returns:
        List of messages: [summary_message, *recent_messages]
    """
    payload = entry.payload
    if not payload:
        return []

    # Support both standardized "summary" and legacy "summary_text"
    summary_text = payload.get("summary", "") or payload.get("summary_text", "")
    if not summary_text:
        return []

    # Create summary message with reason if available
    reason = payload.get("reason", "")
    if reason:
        summary_content = f"[Context Summary - {reason}]\n{summary_text}"
    else:
        summary_content = f"[Context Summary]\n{summary_text}"

    summary_msg = Message(role=Role.USER, content=summary_content)

    # Parse recent_messages if present
    recent_messages: list[Message] = []
    raw_recent = payload.get("recent_messages", [])
    if isinstance(raw_recent, list):
        for raw_msg in raw_recent:
            msg = _raw_message_to_message(raw_msg)
            if msg:
                recent_messages.append(msg)

    return [summary_msg] + recent_messages


def _raw_message_to_message(raw: dict[str, Any]) -> Message | None:
    """Convert a raw dict (from payload) to a Message."""
    if not isinstance(raw, dict):
        return None

    role_str = raw.get("role", "")
    try:
        role = Role(role_str)
    except ValueError:
        return None

    content = raw.get("content", "")

    # Handle structured content
    if isinstance(content, list):
        blocks = []
        for block in content:
            if isinstance(block, dict):
                if "tool_use_id" in block and "name" in block:
                    blocks.append(
                        ToolUseBlock(
                            tool_use_id=block["tool_use_id"],
                            name=block["name"],
                            input=block.get("input", {}),
                        )
                    )
                elif "tool_use_id" in block:
                    blocks.append(
                        ToolResultBlock(
                            tool_use_id=block["tool_use_id"],
                            content=block.get("content", ""),
                            is_error=block.get("is_error", False),
                        )
                    )
        content = blocks if blocks else str(content)

    return Message(role=role, content=content)


def _repair_tool_pairs(messages: list[Message]) -> list[Message]:
    """Repair orphan tool-use and tool-result blocks.

    Ensures:
    - Every tool_use has a matching tool_result
    - Every tool_result has a matching tool_use
    - Tool calls are properly paired in sequence
    """
    if not messages:
        return messages

    # Collect all tool_use_ids from assistant messages
    tool_use_ids: set[str] = set()
    # Collect all tool_result references
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

    # Find orphan tool results (no matching tool_use)
    orphan_results = tool_result_refs - tool_use_ids
    # Find orphan tool uses (no matching tool_result)
    orphan_uses = tool_use_ids - tool_result_refs

    # If no orphans, return as-is
    if not orphan_results and not orphan_uses:
        return messages

    # Repair: remove orphan tool results and add placeholder results for orphan uses
    repaired: list[Message] = []
    for msg in messages:
        if msg.role == Role.TOOL and isinstance(msg.content, list):
            # Filter out orphan tool results
            valid_blocks = [
                b for b in msg.content
                if not isinstance(b, ToolResultBlock) or b.tool_use_id not in orphan_results
            ]
            if valid_blocks:
                msg = Message(role=Role.TOOL, content=valid_blocks)
            else:
                continue
        elif msg.role == Role.ASSISTANT and isinstance(msg.content, list):
            # Keep assistant message but note orphan uses will get placeholder results
            pass

        repaired.append(msg)

    # Add placeholder tool results for orphan tool uses
    for msg in repaired:
        if msg.role == Role.ASSISTANT and isinstance(msg.content, list):
            placeholder_results = []
            for block in msg.content:
                if isinstance(block, ToolUseBlock) and block.tool_use_id in orphan_uses:
                    placeholder_results.append(
                        ToolResultBlock(
                            tool_use_id=block.tool_use_id,
                            content="[Tool result missing from transcript]",
                            is_error=True,
                        )
                    )
            if placeholder_results:
                # Insert placeholder results after the assistant message
                idx = repaired.index(msg)
                repaired.insert(idx + 1, Message(role=Role.TOOL, content=placeholder_results))

    return repaired


def _warn_corrupt_entry(entry: TranscriptEntry, error: Exception) -> None:
    """Log warning for corrupt transcript entry."""
    print(f"Warning: corrupt entry {entry.entry_id}: {error}")
