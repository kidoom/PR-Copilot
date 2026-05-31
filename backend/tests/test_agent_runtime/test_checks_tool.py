from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock

from backend.agent.tools.repo_context.stateless_tools import (
    StatelessReadCheckSummaryTool,
    create_stateless_context_tools,
    create_provider_backed_context_tools,
)
from backend.domain.github.checks_provider import ChecksProviderResult, ChecksSummary


class MockChecksProvider:
    def __init__(self, result: ChecksProviderResult) -> None:
        self._result = result

    async def get_summary(self) -> ChecksProviderResult:
        return self._result


@pytest.mark.asyncio
async def test_read_check_summary_with_provider():
    summary = ChecksSummary(overall_status="success", total_check_runs=0)
    provider = MockChecksProvider(ChecksProviderResult(status="ok", summary=summary))
    tool = StatelessReadCheckSummaryTool(checks_provider=provider)
    result = json.loads(await tool.call({}))
    assert result["status"] == "ok"
    assert result["overall_status"] == "success"


@pytest.mark.asyncio
async def test_read_check_summary_without_provider():
    tool = StatelessReadCheckSummaryTool(checks_provider=None)
    result = json.loads(await tool.call({}))
    assert result["status"] == "unavailable"


@pytest.mark.asyncio
async def test_read_check_summary_non_fatal_error():
    class BrokenProvider:
        async def get_summary(self):
            raise RuntimeError("network error")

    tool = StatelessReadCheckSummaryTool(checks_provider=BrokenProvider())
    result = json.loads(await tool.call({}))
    assert result["status"] == "error"
    assert "network error" in result["reason"]


def test_read_check_summary_input_schema_empty():
    tool = StatelessReadCheckSummaryTool()
    schema = tool.input_schema
    assert schema.get("properties") == {} or not schema.get("required")


def test_read_check_summary_name():
    tool = StatelessReadCheckSummaryTool()
    assert tool.name == "read_check_summary"
    assert tool.is_read_only is True
    assert tool.is_concurrency_safe is True


def test_create_stateless_tools_passes_checks_provider():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        summary = ChecksSummary(overall_status="success")
        provider = MockChecksProvider(ChecksProviderResult(status="ok", summary=summary))
        tools = create_stateless_context_tools(tmp, pr_context=None, checks_provider=provider)
        check_tool = next((t for t in tools if t.name == "read_check_summary"), None)
        assert check_tool is not None
        assert check_tool._checks_provider is provider


def test_create_provider_backed_tools_passes_checks_provider():
    from unittest.mock import MagicMock
    provider_mock = MagicMock()
    provider_mock.repo_root = "/tmp"
    summary = ChecksSummary(overall_status="success")
    checks = MockChecksProvider(ChecksProviderResult(status="ok", summary=summary))
    tools = create_provider_backed_context_tools(provider_mock, pr_context=None, checks_provider=checks)
    check_tool = next((t for t in tools if t.name == "read_check_summary"), None)
    assert check_tool is not None
    assert check_tool._checks_provider is checks
