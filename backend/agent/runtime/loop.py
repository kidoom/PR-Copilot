from __future__ import annotations

import logging
from typing import Any, Callable

from backend.agent.model.messages import Message, ModelResponse, Role, TextDeltaCallback, TokenUsage, ToolResultBlock, ToolUseBlock
from backend.agent.runtime.cancellation import CancellationProbe, check_cancellation
from backend.agent.runtime.trace import ThinkStep, CallStep, ObserveStep, FinalStep, AgentStep
from backend.agent.runtime.results import AgentResult, ToolExecutionResult
from backend.agent.tools.protocol import Tool, ToolConsentFn
from backend.agent.tools.registry import ToolRegistry
from backend.agent.tools.executor import ToolExecutor, ToolExecutorConfig, ToolObservation
from backend.agent.model.client import ModelClient

logger = logging.getLogger(__name__)

# Callback type for message persistence
MessageCallback = Callable[[Message], None]

# Callback type for compact events
CompactCallback = Callable[[str, int, int], None]  # (event_type, before_count, after_count)
ToolEventCallback = Callable[[dict[str, Any]], None]


def _salvage_json_from_messages(messages: list[Message]) -> str | None:
    """Try to extract a valid review-result JSON from conversation history.

    Scans assistant text messages and tool results for JSON containing
    the required 'status' and 'summary' fields.
    """
    import json
    import re

    for msg in reversed(messages):
        candidates: list[str] = []
        if isinstance(msg.content, str) and msg.content.strip():
            candidates.append(msg.content)
        elif isinstance(msg.content, list):
            for block in msg.content:
                if isinstance(block, str) and block.strip():
                    candidates.append(block)

        for text in candidates:
            # Try direct parse
            stripped = text.strip()
            if stripped.startswith("{"):
                try:
                    data = json.loads(stripped)
                    if isinstance(data, dict) and "status" in data and "summary" in data:
                        return stripped
                except (json.JSONDecodeError, ValueError):
                    pass

            # Try extracting from code blocks
            matches = re.findall(r'```(?:json)?\s*\n?([\s\S]*?)\n?```', text)
            for match in matches:
                match = match.strip()
                if match.startswith("{"):
                    try:
                        data = json.loads(match)
                        if isinstance(data, dict) and "status" in data and "summary" in data:
                            return match
                    except (json.JSONDecodeError, ValueError):
                        pass

            # Try finding { ... } with required keys
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                candidate = text[start:end + 1]
                try:
                    data = json.loads(candidate)
                    if isinstance(data, dict) and "status" in data and "summary" in data:
                        return candidate
                except (json.JSONDecodeError, ValueError):
                    pass

    return None


async def run_loop(
    *,
    model: ModelClient,
    tool_registry: ToolRegistry,
    messages: list[Message],
    max_steps: int = 10,
    tool_consent: ToolConsentFn | None = None,
    on_message: MessageCallback | None = None,
    on_tool_call: ToolEventCallback | None = None,
    on_tool_result: ToolEventCallback | None = None,
    on_text_delta: TextDeltaCallback | None = None,
    agent_kind: str = "",
    agent_type: str = "",
    task_id: str = "",
    child_session_id: str = "",
    cancellation_probe: CancellationProbe | None = None,
    # Compression parameters
    session_id: str = "",
    memory_store=None,  # FileMemoryStore | None
    compression_config=None,  # CompressionConfig | None
    compression_profile=None,  # CompactProfile | None
    on_compact: CompactCallback | None = None,
    # ToolExecutor for validated tool execution
    tool_executor: ToolExecutor | None = None,
) -> AgentResult:
    steps: list[AgentStep] = []
    total_usage = TokenUsage()
    tool_schemas = tool_registry.build_schemas()

    # Import compression modules only if compression is enabled
    _compression_enabled = (
        compression_config is not None
        and compression_config.context_compression_enabled
        and session_id
        and memory_store is not None
    )

    for step_num in range(max_steps):
        # Force final output when approaching max steps
        if step_num == max_steps - 2:
            reminder = Message(
                role=Role.USER,
                content=(
                    "[SYSTEM] You have 2 steps remaining. Output your final JSON NOW. "
                    "Do NOT call any more tools. Your next message must be a JSON object like:\n"
                    '{"status":"success","summary":"...","findings":[],"uncertainties":[],"notes":[]}'
                ),
            )
            messages.append(reminder)

        # Build request messages (may be compacted copy)
        request_messages = list(messages)

        # Apply MicroCompact if enabled (only modifies request view)
        if _compression_enabled and compression_config.micro_compact_enabled:
            from backend.agent.runtime.compression.micro_compact import micro_compact_messages
            request_messages = micro_compact_messages(
                request_messages,
                recent_count=compression_config.micro_compact_recent_results,
                min_chars=compression_config.micro_compact_min_chars,
            )

        # AutoCompact check (independent of MicroCompact)
        if _compression_enabled and compression_config.auto_compact_enabled:
            from backend.agent.runtime.compression.estimation import estimate_messages_tokens
            estimated_tokens = estimate_messages_tokens(request_messages)
            if estimated_tokens >= compression_config.auto_compact_threshold:
                # Save before count
                before_count = len(messages)
                try:
                    compact_result = await _execute_compact(
                        messages=messages,
                        model=model,
                        session_id=session_id,
                        memory_store=memory_store,
                        compression_config=compression_config,
                        compression_profile=compression_profile,
                        reason="auto_compact",
                    )
                    if compact_result:
                        # Rewrite runtime messages
                        summary_text, recent_msgs = compact_result
                        from backend.agent.runtime.compression.compact import build_summary_boundary_message
                        boundary_msg = build_summary_boundary_message(summary_text)

                        # Clear and rebuild messages
                        messages.clear()
                        messages.append(boundary_msg)
                        messages.extend(recent_msgs)

                        # Rebuild request
                        request_messages = list(messages)

                        if on_compact:
                            on_compact("auto_compact", before_count, len(messages))

                except Exception as e:
                    # AutoCompact failure is non-fatal
                    logger.warning(f"AutoCompact failed: {e}")

        # Check cancellation before model request (task 4.2)
        check_cancellation(cancellation_probe)

        # Call model - use streaming when on_text_delta callback is provided
        try:
            if on_text_delta is not None:
                response = await model.chat_stream(
                    request_messages,
                    tool_schemas=tool_schemas,
                    on_text_delta=on_text_delta,
                )
            else:
                response = await model.chat(request_messages, tool_schemas=tool_schemas)
        except Exception as e:
            # Check for context-length error
            if _compression_enabled and compression_config.reactive_compact_enabled:
                from backend.agent.runtime.compression.compact import is_context_length_error
                if is_context_length_error(e):
                    # Save before count
                    before_count = len(messages)
                    # ReactiveCompact
                    try:
                        compact_result = await _execute_compact(
                            messages=messages,
                            model=model,
                            session_id=session_id,
                            memory_store=memory_store,
                            compression_config=compression_config,
                            compression_profile=compression_profile,
                            reason="reactive_compact",
                        )
                        if compact_result:
                            summary_text, recent_msgs = compact_result
                            from backend.agent.runtime.compression.compact import build_summary_boundary_message
                            boundary_msg = build_summary_boundary_message(summary_text)

                            messages.clear()
                            messages.append(boundary_msg)
                            messages.extend(recent_msgs)

                            request_messages = list(messages)
                            response = await model.chat(request_messages, tool_schemas=tool_schemas)

                            if on_compact:
                                on_compact("reactive_compact", before_count, len(messages))
                        else:
                            raise
                    except Exception:
                        raise
                else:
                    raise
            else:
                raise

        if response.token_usage:
            total_usage = TokenUsage(
                input_tokens=total_usage.input_tokens + response.token_usage.input_tokens,
                output_tokens=total_usage.output_tokens + response.token_usage.output_tokens,
            )

        if not response.tool_use_blocks:
            steps.append(FinalStep(output=response.content))
            final_msg = Message(role=Role.ASSISTANT, content=response.content)
            messages.append(final_msg)  # Append before callback
            if on_message:
                on_message(final_msg)
            return AgentResult(
                output=response.content,
                steps=steps,
                token_usage=total_usage,
            )

        steps.append(ThinkStep(reasoning=response.content))

        assistant_content: str | list[ToolUseBlock] = response.content
        if response.tool_use_blocks:
            assistant_content = list(response.tool_use_blocks)
        assistant_msg = Message(role=Role.ASSISTANT, content=assistant_content)
        messages.append(assistant_msg)
        if on_message:
            on_message(assistant_msg)

        for block in response.tool_use_blocks:
            # Check cancellation before each tool call (task 4.2)
            check_cancellation(cancellation_probe)

            steps.append(CallStep(
                tool_name=block.name,
                tool_input=block.input,
                tool_use_id=block.tool_use_id,
            ))

            # Use ToolExecutor if available, otherwise fall back to direct invocation
            if tool_executor is not None:
                obs = await tool_executor.execute(block)
                result = obs.content
                is_error = obs.is_error

                steps.append(ObserveStep(
                    tool_use_id=block.tool_use_id,
                    output=result,
                    is_error=is_error,
                ))
                tool_msg = Message(
                    role=Role.TOOL,
                    content=[obs.to_result_block()],
                )
                messages.append(tool_msg)
                if on_message:
                    on_message(tool_msg)
            else:
                # Legacy direct invocation path
                if on_tool_call:
                    on_tool_call({
                        "agent_kind": agent_kind,
                        "agent_type": agent_type,
                        "task_id": task_id,
                        "child_session_id": child_session_id,
                        "tool_name": block.name,
                        "tool_use_id": block.tool_use_id,
                        "input": block.input,
                    })

                tool = tool_registry.resolve(block.name)
                if tool is None:
                    error_output = f"Unknown tool: {block.name}"
                    steps.append(ObserveStep(
                        tool_use_id=block.tool_use_id,
                        output=error_output,
                        is_error=True,
                    ))
                    if on_tool_result:
                        on_tool_result({
                            "agent_kind": agent_kind,
                            "agent_type": agent_type,
                            "task_id": task_id,
                            "child_session_id": child_session_id,
                            "tool_name": block.name,
                            "tool_use_id": block.tool_use_id,
                            "output": error_output,
                            "is_error": True,
                        })
                    tool_msg = Message(
                        role=Role.TOOL,
                        content=[ToolResultBlock(
                            tool_use_id=block.tool_use_id,
                            content=error_output,
                            is_error=True,
                        )],
                    )
                    messages.append(tool_msg)
                    if on_message:
                        on_message(tool_msg)
                    continue

                if tool.requires_consent and tool_consent is not None:
                    approved = await tool_consent(tool, block.input)
                    if not approved:
                        consent_output = f"Tool '{block.name}' requires user consent and was denied."
                        steps.append(ObserveStep(
                            tool_use_id=block.tool_use_id,
                            output=consent_output,
                            is_error=True,
                        ))
                        if on_tool_result:
                            on_tool_result({
                                "agent_kind": agent_kind,
                                "agent_type": agent_type,
                                "task_id": task_id,
                                "child_session_id": child_session_id,
                                "tool_name": block.name,
                                "tool_use_id": block.tool_use_id,
                                "output": consent_output,
                                "is_error": True,
                            })
                        consent_msg = Message(
                            role=Role.TOOL,
                            content=[ToolResultBlock(
                                tool_use_id=block.tool_use_id,
                                content=consent_output,
                                is_error=True,
                            )],
                        )
                        messages.append(consent_msg)
                        if on_message:
                            on_message(consent_msg)
                        continue

                try:
                    result = await tool.call(block.input)
                    is_error = False
                except Exception as exc:
                    result = str(exc)
                    is_error = True

                steps.append(ObserveStep(
                    tool_use_id=block.tool_use_id,
                    output=result,
                    is_error=is_error,
                ))
                if on_tool_result:
                    on_tool_result({
                        "agent_kind": agent_kind,
                        "agent_type": agent_type,
                        "task_id": task_id,
                        "child_session_id": child_session_id,
                        "tool_name": block.name,
                        "tool_use_id": block.tool_use_id,
                        "output": result,
                        "is_error": is_error,
                    })
                tool_msg = Message(
                    role=Role.TOOL,
                    content=[ToolResultBlock(
                        tool_use_id=block.tool_use_id,
                        content=result,
                        is_error=is_error,
                    )],
                )
                messages.append(tool_msg)
                if on_message:
                    on_message(tool_msg)

            # Check cancellation after each tool call (task 4.2)
            check_cancellation(cancellation_probe)

    # Max steps reached — try to salvage any JSON from the conversation
    salvaged = _salvage_json_from_messages(messages)
    if salvaged:
        return AgentResult(
            output=salvaged,
            steps=steps,
            token_usage=total_usage,
            stopped_by_max_steps=True,
        )
    return AgentResult(
        output="Agent stopped: max steps reached without final answer.",
        steps=steps,
        token_usage=total_usage,
        stopped_by_max_steps=True,
    )


async def _execute_compact(
    *,
    messages: list[Message],
    model: ModelClient,
    session_id: str,
    memory_store,
    compression_config,
    compression_profile,
    reason: str = "auto_compact",
) -> tuple[str, list[Message]] | None:
    """Execute compact and persist summary.

    Args:
        reason: "auto_compact" or "reactive_compact"
    """
    from backend.agent.runtime.compression.compact import execute_compact

    result = await execute_compact(
        model=model,
        messages=messages,
        profile=compression_profile,
        config=compression_config,
        memory_store=memory_store,
        session_id=session_id,
    )

    if result:
        summary_text, recent_messages = result

        # Persist summary to memory store
        from backend.agent.runtime.memory.append import append_summary
        from backend.agent.runtime.compression.compact import serialize_recent_messages_lightweight

        lightweight_recent = serialize_recent_messages_lightweight(recent_messages)
        append_summary(memory_store, session_id, {
            "reason": reason,
            "summary": summary_text,
            "before_message_count": len(messages),
            "after_message_count": len(recent_messages) + 1,  # +1 for boundary
            "recent_messages": lightweight_recent,
        })

        return summary_text, recent_messages

    return None
