from backend.agent.runtime.results import AgentResult, ToolExecutionResult
from backend.agent.runtime.trace import FinalStep, ThinkStep


def test_tool_execution_result_defaults():
    r = ToolExecutionResult(tool_use_id="u1", output="ok")
    assert r.is_error is False


def test_tool_execution_result_error():
    r = ToolExecutionResult(tool_use_id="u1", output="fail", is_error=True)
    assert r.is_error is True


def test_agent_result_defaults():
    r = AgentResult(output="answer")
    assert r.steps == []
    assert r.token_usage.input_tokens == 0
    assert r.stopped_by_max_steps is False


def test_agent_result_with_steps():
    steps = [ThinkStep(reasoning="r"), FinalStep(output="done")]
    r = AgentResult(output="done", steps=steps)
    assert len(r.steps) == 2
