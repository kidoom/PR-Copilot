from backend.agent_runtime.model.messages import (
    Message,
    ModelResponse,
    Role,
    TokenUsage,
    ToolResultBlock,
    ToolUseBlock,
)


def test_tool_use_block_fields():
    block = ToolUseBlock(tool_use_id="u1", name="search", input={"q": "test"})
    assert block.tool_use_id == "u1"
    assert block.name == "search"
    assert block.input == {"q": "test"}


def test_tool_result_block_defaults():
    block = ToolResultBlock(tool_use_id="u1", content="ok")
    assert block.is_error is False


def test_tool_result_block_error():
    block = ToolResultBlock(tool_use_id="u1", content="fail", is_error=True)
    assert block.is_error is True


def test_token_usage_defaults():
    usage = TokenUsage()
    assert usage.input_tokens == 0
    assert usage.output_tokens == 0


def test_message_with_string_content():
    msg = Message(role=Role.USER, content="hello")
    assert msg.role == Role.USER
    assert msg.content == "hello"


def test_message_with_blocks():
    block = ToolUseBlock(tool_use_id="u1", name="t", input={})
    msg = Message(role=Role.ASSISTANT, content=[block])
    assert isinstance(msg.content, list)


def test_model_response_defaults():
    resp = ModelResponse(content="answer")
    assert resp.tool_use_blocks == []
    assert resp.token_usage.input_tokens == 0
