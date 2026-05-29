from backend.agent_runtime.runtime.trace import (
    CallStep,
    FinalStep,
    ObserveStep,
    StepKind,
    ThinkStep,
)


def test_think_step_default_kind():
    step = ThinkStep(reasoning="considering options")
    assert step.kind == StepKind.THINK
    assert step.reasoning == "considering options"


def test_call_step_fields():
    step = CallStep(tool_name="search", tool_input={"q": "x"}, tool_use_id="u1")
    assert step.kind == StepKind.CALL
    assert step.tool_name == "search"
    assert step.tool_input == {"q": "x"}


def test_observe_step_defaults():
    step = ObserveStep(tool_use_id="u1", output="result")
    assert step.kind == StepKind.OBSERVE
    assert step.is_error is False


def test_observe_step_error():
    step = ObserveStep(tool_use_id="u1", output="boom", is_error=True)
    assert step.is_error is True


def test_final_step():
    step = FinalStep(output="done")
    assert step.kind == StepKind.FINAL
    assert step.output == "done"
