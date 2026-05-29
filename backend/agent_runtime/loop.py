from __future__ import annotations

from .models import Message, ModelResponse, Role, ToolResultBlock, ToolUseBlock, TokenUsage
from .trace import ThinkStep, CallStep, ObserveStep, FinalStep, AgentStep
from .results import AgentResult, ToolExecutionResult
from .tool import Tool
from .registry import ToolRegistry
from .model_client import ModelClient


async def run_loop(
    *,
    model: ModelClient,
    tool_registry: ToolRegistry,
    messages: list[Message],
    max_steps: int = 10,
) -> AgentResult:
    steps: list[AgentStep] = []
    total_usage = TokenUsage()

    for step_num in range(max_steps):
        response = await model.chat(messages)

        if response.token_usage:
            total_usage = TokenUsage(
                input_tokens=total_usage.input_tokens + response.token_usage.input_tokens,
                output_tokens=total_usage.output_tokens + response.token_usage.output_tokens,
            )

        if not response.tool_use_blocks:
            steps.append(FinalStep(output=response.content))
            return AgentResult(
                output=response.content,
                steps=steps,
                token_usage=total_usage,
            )

        steps.append(ThinkStep(reasoning=response.content))

        messages.append(Message(role=Role.ASSISTANT, content=response.content))

        for block in response.tool_use_blocks:
            steps.append(CallStep(
                tool_name=block.name,
                tool_input=block.input,
                tool_use_id=block.tool_use_id,
            ))

            tool = tool_registry.resolve(block.name)
            if tool is None:
                error_output = f"Unknown tool: {block.name}"
                steps.append(ObserveStep(
                    tool_use_id=block.tool_use_id,
                    output=error_output,
                    is_error=True,
                ))
                messages.append(Message(
                    role=Role.TOOL,
                    content=[ToolResultBlock(
                        tool_use_id=block.tool_use_id,
                        content=error_output,
                        is_error=True,
                    )],
                ))
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
            messages.append(Message(
                role=Role.TOOL,
                content=[ToolResultBlock(
                    tool_use_id=block.tool_use_id,
                    content=result,
                    is_error=is_error,
                )],
            ))

    return AgentResult(
        output="Agent stopped: max steps reached without final answer.",
        steps=steps,
        token_usage=total_usage,
        stopped_by_max_steps=True,
    )
