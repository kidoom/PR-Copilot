from __future__ import annotations

import json
import os
import tempfile
import pytest

from backend.agent.runtime.cancellation import CancellationProbe
from backend.agent.tools.repo_context.stateless_tools import (
    StatelessSearchRepoTool,
    StatelessSearchTestsForTool,
)


@pytest.mark.asyncio
async def test_search_repo_observes_cancellation():
    probe = CancellationProbe()
    probe.cancel()
    with tempfile.TemporaryDirectory() as tmp:
        tool = StatelessSearchRepoTool(tmp, cancellation_probe=probe)
        result = json.loads(await tool.call({"query": "test"}))
        assert result.get("cancelled") is True


@pytest.mark.asyncio
async def test_search_tests_for_observes_cancellation():
    probe = CancellationProbe()
    probe.cancel()
    with tempfile.TemporaryDirectory() as tmp:
        tool = StatelessSearchTestsForTool(tmp, cancellation_probe=probe)
        result = json.loads(await tool.call({"source_file": "foo.py"}))
        assert result.get("status") == "cancelled"


@pytest.mark.asyncio
async def test_search_repo_works_without_probe():
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "test.py"), "w") as f:
            f.write("hello world\n")
        tool = StatelessSearchRepoTool(tmp, cancellation_probe=None)
        result = json.loads(await tool.call({"query": "hello"}))
        assert result["total"] >= 1
        assert "cancelled" not in result


@pytest.mark.asyncio
async def test_search_repo_not_cancelled_during_normal_run():
    probe = CancellationProbe()
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "test.py"), "w") as f:
            f.write("foo bar\n")
        tool = StatelessSearchRepoTool(tmp, cancellation_probe=probe)
        result = json.loads(await tool.call({"query": "foo"}))
        assert result["total"] >= 1
        assert "cancelled" not in result
