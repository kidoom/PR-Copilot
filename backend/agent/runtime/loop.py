from __future__ import annotations

from backend.agent.model.messages import Message, ModelResponse, Role, TokenUsage, ToolResultBlock, ToolUseBlock
from backend.agent.runtime.trace import ThinkStep, CallStep, ObserveStep, FinalStep, AgentStep
from backend.agent.runtime.results import AgentResult, ToolExecutionResult
from backend.agent.tools.protocol import Tool, ToolConsentFn
from backend.agent.tools.registry import ToolRegistry
from backend.agent.model.client import ModelClient


async def run_loop(
    *,
    model: ModelClient,
    tool_registry: ToolRegistry,
    messages: list[Message],
    max_steps: int = 10,
    tool_consent: ToolConsentFn | None = None,
) -> AgentResult:
    steps: list[AgentStep] = []
    total_usage = TokenUsage()
    tool_schemas = tool_registry.build_schemas()

    for step_num in range(max_steps):
        response = await model.chat(messages, tool_schemas=tool_schemas)

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

        assistant_content: str | list[ToolUseBlock] = response.content
        if response.tool_use_blocks:
            assistant_content = list(response.tool_use_blocks)
        messages.append(Message(role=Role.ASSISTANT, content=assistant_content))

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

            if tool.requires_consent and tool_consent is not None:
                approved = await tool_consent(tool, block.input)
                if not approved:
                    consent_output = f"Tool '{block.name}' requires user consent and was denied."
                    steps.append(ObserveStep(
                        tool_use_id=block.tool_use_id,
                        output=consent_output,
                        is_error=True,
                    ))
                    messages.append(Message(
                        role=Role.TOOL,
                        content=[ToolResultBlock(
                            tool_use_id=block.tool_use_id,
                            content=consent_output,
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
