from __future__ import annotations

import json
import pytest

from backend.agent.runtime.cancellation import CancellationProbe, Cancelled
from backend.agent.runtime.sub_agent import SubAgentResult
from backend.agent.tools.task import TaskTool


async def fake_runner(*, prompt, agent_type, max_steps=None, task=None):
    return SubAgentResult(
        output=json.dumps({
            "status": "success",
            "summary": "ok",
            "findings": [{
                "claim": "test finding",
                "confidence": 0.8,
                "severity": "medium",
                "evidence": [{"file": "test.py", "line": 1, "snippet": "code", "source": "file"}],
            }],
            "uncertainties": [],
            "notes": [],
        }),
        agent_type=agent_type,
    )


async def slow_runner(*, prompt, agent_type, max_steps=None, task=None):
    import asyncio
    await asyncio.sleep(10)
    return SubAgentResult(output="never", agent_type=agent_type)


@pytest.mark.asyncio
async def test_batch_dispatch_stops_on_cancellation():
    probe = CancellationProbe()
    probe.cancel()
    tool = TaskTool(runner=fake_runner, agent_types=["reviewer"], cancellation_probe=probe)
    result = await tool.call({
        "task_plan": {
            "tasks": [
                {"task_id": "t1", "task_type": "review", "agent_type": "reviewer"},
                {"task_id": "t2", "task_type": "review", "agent_type": "reviewer"},
            ],
            "routes": [],
        }
    })
    parsed = json.loads(result)
    results = parsed["results"]
    assert all(r["status"] == "cancelled" for r in results)


@pytest.mark.asyncio
async def test_batch_dispatch_without_probe_works():
    tool = TaskTool(runner=fake_runner, agent_types=["reviewer"], cancellation_probe=None)
    result = await tool.call({
        "task_plan": {
            "tasks": [{"task_id": "t1", "task_type": "review", "agent_type": "reviewer"}],
            "routes": [],
        }
    })
    parsed = json.loads(result)
    assert parsed["dispatched"] == 1
    assert len(parsed["results"]) == 1
    assert parsed["results"][0]["status"] == "ok"
